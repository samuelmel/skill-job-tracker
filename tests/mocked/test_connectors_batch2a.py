"""Testes mockados (respx) dos conectores do lote 2A: parse, paginacao e complete=False."""

import json
from typing import Any

import httpx
import pytest
import respx

from vaya_indexer.connectors.applicantpro import ApplicantProConnector
from vaya_indexer.connectors.avature import AvatureConnector
from vaya_indexer.connectors.careerplug import CareerPlugConnector
from vaya_indexer.connectors.comeet import ComeetConnector
from vaya_indexer.connectors.eightfold_ai import EightfoldConnector
from vaya_indexer.connectors.icims import IcimsConnector
from vaya_indexer.connectors.jazzhr import JazzHRConnector
from vaya_indexer.connectors.jobvite import JobviteConnector
from vaya_indexer.connectors.join_com import JoinConnector
from vaya_indexer.connectors.oracle_recruiting import OracleRecruitingConnector
from vaya_indexer.connectors.paylocity import PaylocityConnector
from vaya_indexer.core.exceptions import ParseError
from vaya_indexer.extraction.http import HttpClient

GUID = "cab4161a-b9de-40e7-85e8-27920748af9d"


def _ld(items: list[dict[str, Any]], number: int | None = None) -> str:
    data = {
        "@type": "ItemList",
        "numberOfItems": len(items) if number is None else number,
        "itemListElement": [{"@type": "ListItem", "item": i} for i in items],
    }
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


def _posting(i: int) -> dict[str, Any]:
    return {"@type": "JobPosting", "title": f"Job {i}", "identifier": {"value": str(i)}}


@respx.mock
async def test_comeet_reads_public_token_from_careers_page() -> None:
    page = (
        '<script>fetch("https://www.comeet.co/careers-api/2.0/company/43.001/'
        'positions/?token=ABC123&x=1")</script>'
    )
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text=page))
    api = respx.get("https://www.comeet.co/careers-api/2.0/company/43.001/positions").mock(
        return_value=httpx.Response(200, json=[{"uid": "1", "name": "A"}, {"name": "sem uid"}])
    )
    async with HttpClient(user_agent="t") as http:
        result = await ComeetConnector(
            http, company_uid="43.001", careers_url="https://example.com/careers"
        ).fetch_jobs()
    assert api.calls[0].request.url.params["token"] == "ABC123"
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False


@respx.mock
async def test_comeet_without_token_is_parse_error() -> None:
    respx.get("https://example.com/careers").mock(return_value=httpx.Response(200, text="<html/>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await ComeetConnector(
                http, company_uid="43.001", careers_url="https://example.com/careers"
            ).fetch_jobs()


@respx.mock
async def test_applicantpro_truncated_at_cap_is_incomplete() -> None:
    url = "https://acme.applicantpro.com/core/jobs/7"
    jobs = [{"id": i, "title": "T"} for i in range(1, 4)]
    respx.get(url).mock(
        return_value=httpx.Response(200, json={"data": {"jobs": jobs, "jobCount": 877}})
    )
    async with HttpClient(user_agent="t") as http:
        result = await ApplicantProConnector(http, subdomain="acme", domain_id=7).fetch_jobs()
    assert len(result.jobs) == 3
    assert result.complete is False
    respx.get(url).mock(
        return_value=httpx.Response(200, json={"data": {"jobs": jobs, "jobCount": 3}})
    )
    async with HttpClient(user_agent="t") as http:
        result = await ApplicantProConnector(http, subdomain="acme", domain_id=7).fetch_jobs()
    assert result.complete is True


@respx.mock
async def test_applicantpro_unexpected_shape() -> None:
    respx.get("https://acme.applicantpro.com/core/jobs/7").mock(
        return_value=httpx.Response(200, json={"foo": 1})
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await ApplicantProConnector(http, subdomain="acme", domain_id=7).fetch_jobs()


@respx.mock
async def test_careerplug_item_list_and_count_mismatch() -> None:
    url = "https://acme.careerplug.com/jobs"
    respx.get(url).mock(return_value=httpx.Response(200, text=_ld([_posting(1), _posting(2)])))
    async with HttpClient(user_agent="t") as http:
        result = await CareerPlugConnector(http, subdomain="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1", "2"]
    assert result.complete is True
    respx.get(url).mock(return_value=httpx.Response(200, text=_ld([_posting(1)], number=3)))
    async with HttpClient(user_agent="t") as http:
        result = await CareerPlugConnector(http, subdomain="acme").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_careerplug_empty_marker_and_unknown_page() -> None:
    url = "https://acme.careerplug.com/jobs"
    respx.get(url).mock(
        return_value=httpx.Response(200, text="<p>There are no jobs posted at this time</p>")
    )
    async with HttpClient(user_agent="t") as http:
        result = await CareerPlugConnector(http, subdomain="acme").fetch_jobs()
    assert result.jobs == []
    assert result.complete is True
    respx.get(url).mock(return_value=httpx.Response(200, text="<p>Sign in</p>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await CareerPlugConnector(http, subdomain="acme").fetch_jobs()


@respx.mock
async def test_eightfold_offset_pagination_with_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        ids = range(start + 1, min(start + 10, 12) + 1)
        positions = [{"id": i, "name": f"J{i}"} for i in ids]
        return httpx.Response(200, json={"count": 12, "positions": positions})

    respx.get("https://acme.eightfold.ai/api/apply/v2/jobs").mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await EightfoldConnector(http, tenant="acme", domain="acme.com").fetch_jobs()
    assert len(result.jobs) == 12
    assert result.complete is True


@respx.mock
async def test_eightfold_total_larger_than_collected_is_incomplete() -> None:
    body = {"count": 50, "positions": [{"id": 1, "name": "J"}]}
    respx.get("https://acme.eightfold.ai/api/apply/v2/jobs").mock(
        return_value=httpx.Response(200, json=body)
    )
    async with HttpClient(user_agent="t") as http:
        result = await EightfoldConnector(http, tenant="acme", domain="acme.com").fetch_jobs()
    assert result.complete is False


ORACLE_HOST = "h.fa.us2.oraclecloud.com"


@respx.mock
async def test_oracle_paginates_requisition_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["finder"].split("offset=")[1])
        ids = range(offset + 1, min(offset + 50, 60) + 1)
        reqs = [{"Id": str(i), "Title": f"J{i}"} for i in ids]
        body = {"items": [{"TotalJobsCount": 60, "requisitionList": reqs}]}
        return httpx.Response(200, json=body)

    respx.route(url__startswith=f"https://{ORACLE_HOST}/").mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await OracleRecruitingConnector(
            http, host=ORACLE_HOST, site_number="CX_1"
        ).fetch_jobs()
    assert len(result.jobs) == 60
    assert result.complete is True


@respx.mock
async def test_oracle_unexpected_shape() -> None:
    respx.route(url__startswith=f"https://{ORACLE_HOST}/").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await OracleRecruitingConnector(http, host=ORACLE_HOST, site_number="CX_1").fetch_jobs()


def _join_html(items: list[dict[str, Any]], total: int, page_count: int = 1) -> str:
    jobs = {"items": items, "pagination": {"total": total, "pageCount": page_count}}
    data = {"props": {"pageProps": {"pageData": {"jobs": jobs}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


@respx.mock
async def test_join_total_mismatch_is_incomplete() -> None:
    url = "https://join.com/companies/acme"
    items = [{"id": 1, "idParam": "1-a", "title": "A"}]
    respx.get(url).mock(return_value=httpx.Response(200, text=_join_html(items, 1)))
    async with HttpClient(user_agent="t") as http:
        assert (await JoinConnector(http, company="acme").fetch_jobs()).complete is True
    respx.get(url).mock(return_value=httpx.Response(200, text=_join_html(items, 4)))
    async with HttpClient(user_agent="t") as http:
        assert (await JoinConnector(http, company="acme").fetch_jobs()).complete is False


@respx.mock
async def test_join_without_next_data_is_parse_error() -> None:
    respx.get("https://join.com/companies/acme").mock(return_value=httpx.Response(200, text="<p/>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await JoinConnector(http, company="acme").fetch_jobs()


@respx.mock
async def test_jobvite_table_and_missing_table() -> None:
    url = "https://jobs.jobvite.com/acme/jobs"
    html = (
        '<table class="jv-job-list"><tr><td class="jv-job-list-name">'
        '<a href="/acme/job/oAb1">A</a></td>'
        '<td class="jv-job-list-location">Camas,\n Washington</td></tr></table>'
    )
    respx.get(url).mock(return_value=httpx.Response(200, text=html))
    async with HttpClient(user_agent="t") as http:
        result = await JobviteConnector(http, company="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["oAb1"]
    assert result.jobs[0].payload["location"] == "Camas, Washington"
    respx.get(url).mock(return_value=httpx.Response(200, text="<p>Support</p>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await JobviteConnector(http, company="acme").fetch_jobs()


@respx.mock
async def test_jazzhr_parse_and_redirect_to_marketing_site() -> None:
    html = (
        '<li class="list-group-item"><h3>'
        '<a href="https://acme.applytojob.com/apply/AbC123/Dev">Dev</a></h3>'
        '<ul><li><i class="fa fa-map-marker"></i> Remote</li></ul></li>'
    )
    respx.get("https://acme.applytojob.com/apply").mock(return_value=httpx.Response(200, text=html))
    async with HttpClient(user_agent="t") as http:
        result = await JazzHRConnector(http, subdomain="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["AbC123"]
    assert result.jobs[0].payload["location"] == "Remote"
    respx.get("https://gone.applytojob.com/apply").mock(
        return_value=httpx.Response(302, headers={"location": "https://www.jazzhr.com/job-seekers"})
    )
    respx.get("https://www.jazzhr.com/job-seekers").mock(
        return_value=httpx.Response(200, text="<p>JazzHR</p>")
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await JazzHRConnector(http, subdomain="gone").fetch_jobs()


def _icims_page(ids: list[int], pages: int) -> str:
    cards = "".join(
        f'<li class="iCIMS_JobCardItem"><div class="row"><div class="title">'
        f'<a href="https://h.icims.com/jobs/{i}/x/job"><h3>J{i}</h3></a></div>'
        f'<div class="iCIMS_JobHeaderTag"><dt>Location : Name</dt><dd><span>Fresno</span></dd>'
        f"</div></div></li>"
        for i in ids
    )
    return f"<html><body><ul>{cards}</ul><span>Page 1 of {pages}</span></body></html>"


@respx.mock
async def test_icims_paginates_by_pr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pr = int(request.url.params["pr"])
        return httpx.Response(200, text=_icims_page([pr * 2 + 1, pr * 2 + 2], 3))

    respx.get("https://h.icims.com/jobs/search").mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await IcimsConnector(http, host="h.icims.com").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1", "2", "3", "4", "5", "6"]
    assert result.complete is True


@respx.mock
async def test_icims_page_without_cards_is_parse_error() -> None:
    respx.get("https://h.icims.com/jobs/search").mock(return_value=httpx.Response(200, text="<p/>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await IcimsConnector(http, host="h.icims.com").fetch_jobs()


@respx.mock
async def test_paylocity_page_data_skips_internal() -> None:
    jobs = [
        {"JobId": 1, "JobTitle": "A", "IsInternal": False},
        {"JobId": 2, "JobTitle": "B", "IsInternal": True},
        {"JobTitle": "sem id"},
    ]
    html = f"<script>\nwindow.pageData = {json.dumps({'Jobs': jobs})};\n</script>"
    respx.get(f"https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}").mock(
        return_value=httpx.Response(200, text=html)
    )
    async with HttpClient(user_agent="t") as http:
        result = await PaylocityConnector(http, guid=GUID).fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False


@respx.mock
async def test_paylocity_without_page_data() -> None:
    respx.get(f"https://recruiting.paylocity.com/recruiting/jobs/All/{GUID}").mock(
        return_value=httpx.Response(200, text="<p/>")
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await PaylocityConnector(http, guid=GUID).fetch_jobs()


FEED = (
    '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>'
    "<item><title>A</title><link>https://h.example.com/en_US/main/JobDetail/X-A/10</link>"
    "<guid>https://h.example.com/en_US/main/JobDetail/X-A/10</guid>"
    "<description>Brazil - 55</description></item></channel></rss>"
)


@respx.mock
async def test_avature_total_from_html_decides_completeness() -> None:
    base = "https://h.example.com/en_US/main/SearchJobs"
    respx.get(f"{base}/feed/").mock(return_value=httpx.Response(200, text=FEED))
    page = respx.get(base).mock(return_value=httpx.Response(200, text="<p>1-1\n of 1</p>"))
    async with HttpClient(user_agent="t") as http:
        result = await AvatureConnector(http, host="h.example.com", path="en_US/main").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["10"]
    assert result.complete is True
    page.mock(return_value=httpx.Response(200, text="<p>1-20 of 336</p>"))
    async with HttpClient(user_agent="t") as http:
        result = await AvatureConnector(http, host="h.example.com", path="en_US/main").fetch_jobs()
    assert result.complete is False
