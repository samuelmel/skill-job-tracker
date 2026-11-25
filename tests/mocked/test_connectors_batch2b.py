"""Testes mockados (respx) dos conectores do lote 2B: parse, paginacao e complete=False."""

import json
from typing import Any

import httpx
import pytest
import respx

from skilometer.connectors.hireology import HireologyConnector
from skilometer.connectors.hiringthing import HiringThingConnector
from skilometer.connectors.isolvedhire import IsolvedHireConnector
from skilometer.connectors.pageup import PageUpConnector
from skilometer.connectors.personio import PersonioConnector
from skilometer.connectors.phenom_people import PhenomPeopleConnector
from skilometer.connectors.recooty import RecootyConnector
from skilometer.connectors.teamtailor import TeamtailorConnector
from skilometer.connectors.trakstar import TrakstarConnector
from skilometer.connectors.trinet import TriNetConnector
from skilometer.connectors.zoho_recruit import ZohoRecruitConnector
from skilometer.core.exceptions import ConnectorConfigError, ParseError
from skilometer.extraction.http import HttpClient


def _rss(items: str, ns: str = "") -> str:
    return f'<?xml version="1.0"?><rss version="2.0" {ns}><channel>{items}</channel></rss>'


def _jsonld(posting: dict[str, Any]) -> str:
    body = json.dumps({"@context": "https://schema.org", **posting})
    return f'<html><script type="application/ld+json">{body}</script></html>'


# -- formato inesperado -> ParseError -------------------------------------------------------

SHAPE_CASES = [
    (PersonioConnector, {"account": "acme"}, "https://acme.jobs.personio.com/xml", "<html/>"),
    (PageUpConnector, {"client_id": "1"}, "https://careers.pageuppeople.com/1/cw/en/rss", "nope"),
    (HiringThingConnector, {"subdomain": "acme"}, "https://acme.hiringthing.com/api/rss.xml", "x"),
    (TeamtailorConnector, {"company": "acme"}, "https://acme.teamtailor.com/jobs.rss", "x"),
    (
        IsolvedHireConnector,
        {"subdomain": "acme"},
        "https://acme.isolvedhire.com/job_site_map.xml",
        "x",
    ),
    (TrakstarConnector, {"company": "acme"}, "https://acme.hire.trakstar.com/sitemap.xml", "x"),
    (
        ZohoRecruitConnector,
        {"company": "acme"},
        "https://acme.zohorecruit.com/jobs/Careers",
        "<p/>",
    ),
]


@pytest.mark.parametrize(("cls", "config", "url", "body"), SHAPE_CASES)
@respx.mock
async def test_unexpected_shape_raises_parse_error(cls, config, url, body) -> None:  # type: ignore[no-untyped-def]
    respx.get(url__startswith=url).mock(return_value=httpx.Response(200, text=body))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await cls(http, **config).fetch_jobs()


@respx.mock
async def test_phenom_unexpected_shape_raises_parse_error() -> None:
    respx.post("https://careers.acme.example/widgets").mock(
        return_value=httpx.Response(200, json={"foo": 1})
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await PhenomPeopleConnector(http, domain="careers.acme.example").fetch_jobs()


@respx.mock
async def test_hireology_declared_jobs_without_cards_raises_parse_error() -> None:
    respx.get("https://careers.hireology.com/acme").mock(
        return_value=httpx.Response(200, text="<html><body>12 jobs found</body></html>")
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await HireologyConnector(http, slug="acme").fetch_jobs()


# -- configs inseguras ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "config"),
    [
        (PersonioConnector, {"account": "a/b"}),
        (PersonioConnector, {"account": "acme", "tld": "evil.com"}),
        (PageUpConnector, {"client_id": "12/../x"}),
        (HiringThingConnector, {"subdomain": "a.evil.com"}),
        (TeamtailorConnector, {"company": "acme/../x"}),
        (IsolvedHireConnector, {"subdomain": "a@b"}),
        (TrakstarConnector, {"company": "a b"}),
        (HireologyConnector, {"slug": "../x"}),
        (RecootyConnector, {"company": "a/b"}),
        (TriNetConnector, {"company": "abc-x"}),
        (ZohoRecruitConnector, {"company": "a.evil"}),
        (PhenomPeopleConnector, {"domain": "evil.com/path"}),
    ],
)
def test_config_rejects_unsafe_values(cls, config) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConnectorConfigError):
        cls.validate_config(config)


# -- Personio ------------------------------------------------------------------------------


@respx.mock
async def test_personio_parses_positions_and_flags_incomplete() -> None:
    xml = (
        '<?xml version="1.0"?><workzag-jobs>'
        "<position><id>1</id><name>Dev</name><office>Berlin</office>"
        "<additionalOffices><office>Munich</office></additionalOffices>"
        "<schedule>full-time</schedule>"
        "<jobDescriptions><jobDescription><name>Tasks</name><value>&lt;p&gt;x&lt;/p&gt;</value>"
        "</jobDescription></jobDescriptions></position>"
        "<position><name>Sem id</name></position></workzag-jobs>"
    )
    respx.get("https://acme.jobs.personio.com/xml").mock(return_value=httpx.Response(200, text=xml))
    async with HttpClient(user_agent="t") as http:
        result = await PersonioConnector(http, account="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False
    assert result.jobs[0].payload["additionalOffices"] == ["Munich"]


# -- RSS (PageUp, HiringThing, Teamtailor) -------------------------------------------------


@respx.mock
async def test_pageup_reads_extension_fields() -> None:
    ns = 'xmlns:job="http://pageuppeople.com/"'
    item = (
        "<item><guid>g1</guid><title>Eng</title><link>https://x.test/1</link>"
        "<job:refNo>R1</job:refNo><job:location>Cardiff</job:location>"
        "<job:workType>Full time</job:workType><job:description>&lt;p&gt;d&lt;/p&gt;"
        "</job:description></item>"
    )
    respx.get("https://careers.pageuppeople.com/9/cw/en/rss").mock(
        return_value=httpx.Response(200, text=_rss(item, ns))
    )
    async with HttpClient(user_agent="t") as http:
        result = await PageUpConnector(http, client_id="9").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["R1"]
    assert result.jobs[0].payload["location"] == "Cardiff"
    assert result.complete is True


@respx.mock
async def test_hiringthing_duplicate_ids_flag_incomplete() -> None:
    item = "<item><guid>same</guid><title>A</title><link>https://x.test/a</link></item>"
    respx.get("https://acme.hiringthing.com/api/rss.xml").mock(
        return_value=httpx.Response(200, text=_rss(item * 2))
    )
    async with HttpClient(user_agent="t") as http:
        result = await HiringThingConnector(http, subdomain="acme").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_teamtailor_reads_locations_and_remote_status() -> None:
    ns = 'xmlns:tt="https://teamtailor.com/locations"'
    item = (
        "<item><guid>u1</guid><title>Analyst</title><link>https://x.test/1</link>"
        "<remoteStatus>hybrid</remoteStatus><tt:locations><tt:location><tt:name>HQ</tt:name>"
        "<tt:city>Berlin</tt:city><tt:country>Germany</tt:country></tt:location></tt:locations>"
        "</item>"
    )
    respx.get("https://acme.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(200, text=_rss(item, ns))
    )
    async with HttpClient(user_agent="t") as http:
        result = await TeamtailorConnector(http, company="acme").fetch_jobs()
    assert result.jobs[0].payload["remoteStatus"] == "hybrid"
    assert result.jobs[0].payload["locations"][0]["city"] == "Berlin"
    connector = TeamtailorConnector.__new__(TeamtailorConnector)
    assert connector.normalize(result.jobs[0]).workplace_type == "HYBRID"


# -- Sitemap + JSON-LD (iSolvedHire, Trakstar) ---------------------------------------------


def _sitemap(urls: list[str]) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


@respx.mock
async def test_isolvedhire_fetches_details_and_flags_failed_detail() -> None:
    base = "https://acme.isolvedhire.com"
    respx.get(f"{base}/job_site_map.xml").mock(
        return_value=httpx.Response(
            200, text=_sitemap([f"{base}/jobs/1.html", f"{base}/jobs/2.html", f"{base}/about"])
        )
    )
    respx.get(f"{base}/jobs/1.html").mock(
        return_value=httpx.Response(200, text=_jsonld({"@type": "JobPosting", "title": "A"}))
    )
    respx.get(f"{base}/jobs/2.html").mock(return_value=httpx.Response(200, text="<html/>"))
    async with HttpClient(user_agent="t") as http:
        result = await IsolvedHireConnector(http, subdomain="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False


@respx.mock
async def test_trakstar_reads_jsonld_and_caps_are_incomplete(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import skilometer.connectors.trakstar as mod

    base = "https://acme.hire.trakstar.com"
    respx.get(f"{base}/sitemap.xml").mock(
        return_value=httpx.Response(
            200, text=_sitemap([f"{base}/jobs/aa1", f"{base}/jobs/aa2", f"{base}/jobs/aa3"])
        )
    )
    respx.get(url__regex=rf"{base}/jobs/aa\d").mock(
        return_value=httpx.Response(
            200, text=_jsonld({"@type": "JobPosting", "title": "T", "description": "&lt;p&gt;x"})
        )
    )
    async with HttpClient(user_agent="t") as http:
        full = await TrakstarConnector(http, company="acme").fetch_jobs()
        assert len(full.jobs) == 3
        assert full.complete is True
        monkeypatch.setattr(mod, "MAX_JOBS", 2)
        capped = await TrakstarConnector(http, company="acme").fetch_jobs()
    assert len(capped.jobs) == 2
    assert capped.complete is False


# -- HTML (Hireology, Recooty, TriNet) -----------------------------------------------------


def _hireology_page(ids: list[int], declared: int) -> str:
    cards = "".join(
        f'<li data-qa-id="job-card"><a data-qa-id="job-card-title" href="/acme/{i}/description">'
        f'Job {i}</a><span data-qa-id="job-card-location">Austin, TX</span></li>'
        for i in ids
    )
    return f"<html><body><h2>{declared} jobs found</h2><ul>{cards}</ul></body></html>"


@respx.mock
async def test_hireology_paginates_and_checks_declared_total() -> None:
    route = respx.get("https://careers.hireology.com/acme")
    route.side_effect = [
        httpx.Response(200, text=_hireology_page(list(range(1, 11)), 12)),
        httpx.Response(200, text=_hireology_page([11, 12], 12)),
        httpx.Response(200, text=_hireology_page([], 12)),
    ]
    async with HttpClient(user_agent="t") as http:
        result = await HireologyConnector(http, slug="acme").fetch_jobs()
    assert len(result.jobs) == 12
    assert result.complete is True


@respx.mock
async def test_hireology_fewer_jobs_than_declared_is_incomplete() -> None:
    route = respx.get("https://careers.hireology.com/acme")
    route.side_effect = [
        httpx.Response(200, text=_hireology_page([1, 2], 5)),
        httpx.Response(200, text=_hireology_page([], 5)),
    ]
    async with HttpClient(user_agent="t") as http:
        result = await HireologyConnector(http, slug="acme").fetch_jobs()
    assert len(result.jobs) == 2
    assert result.complete is False


@respx.mock
async def test_recooty_listing_and_detail() -> None:
    page = (
        "<html><body><div><h3><a href='https://jobs.recooty.com/acme/dev-at1'>Dev</a></h3>"
        "<ul><li><i class='fa-location-arrow'></i> [Remote]</li>"
        "<li><i class='fa-briefcase'></i> Full Time</li></ul></div>"
        "<div><h3><a href='https://jobs.recooty.com/other/x-at2'>Outra empresa</a></h3></div>"
        "</body></html>"
    )
    respx.get("https://jobs.recooty.com/acme").mock(
        side_effect=[httpx.Response(200, text=page), httpx.Response(200, text="<html/>")]
    )
    respx.get("https://jobs.recooty.com/acme/dev-at1").mock(
        return_value=httpx.Response(200, text=_jsonld({"@type": "JobPosting", "title": "Dev"}))
    )
    async with HttpClient(user_agent="t") as http:
        result = await RecootyConnector(http, company="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["dev-at1"]
    assert result.jobs[0].payload["location"] == "Remote"
    assert result.jobs[0].payload["posting"]["title"] == "Dev"


@respx.mock
async def test_trinet_listing_and_detail_failure_is_incomplete() -> None:
    base = "https://app.trinethire.com"
    listing = (
        "<table class='job-list'>"
        "<tr class='job'><td><a href='/companies/7-acme/jobs/11-dev'>Dev</a></td>"
        "<td class='type'>Full-time</td><td class='location'>Remote</td></tr>"
        "<tr class='job'><td><a href='/companies/7-acme/jobs/12-ops'>Ops</a></td></tr>"
        "<tr class='job'><td><a href='/companies/9-other/jobs/13-x'>Fora</a></td></tr>"
        "</table>"
    )
    respx.get(f"{base}/companies/7-acme/jobs").mock(return_value=httpx.Response(200, text=listing))
    respx.get(f"{base}/companies/7-acme/jobs/11-dev").mock(
        return_value=httpx.Response(200, text="<div class='job-descr content'><p>Desc</p></div>")
    )
    respx.get(f"{base}/companies/7-acme/jobs/12-ops").mock(
        return_value=httpx.Response(200, text="<html/>")
    )
    async with HttpClient(user_agent="t") as http:
        result = await TriNetConnector(http, company="7-acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["11", "12"]
    assert "Desc" in (result.jobs[0].payload["description"] or "")
    assert result.jobs[1].payload["description"] is None
    assert result.complete is False


# -- Zoho Recruit --------------------------------------------------------------------------


@respx.mock
async def test_zoho_skips_unpublished_and_locked() -> None:
    jobs = [
        {"id": "1", "Posting_Title": "A", "Publish": True},
        {"id": "2", "Posting_Title": "B", "Publish": False},
        {"id": "3", "Posting_Title": "C", "Publish": True, "Is_Locked": True},
    ]
    value = json.dumps(jobs).replace('"', "&quot;")
    respx.get("https://acme.zohorecruit.com/jobs/Careers").mock(
        return_value=httpx.Response(200, text=f'<html><input id="jobs" value="{value}"></html>')
    )
    async with HttpClient(user_agent="t") as http:
        result = await ZohoRecruitConnector(http, company="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is True


# -- Phenom People -------------------------------------------------------------------------


def _phenom(jobs: list[dict[str, Any]], total: int) -> dict[str, Any]:
    return {"refineSearch": {"totalHits": total, "data": {"jobs": jobs}}}


@respx.mock
async def test_phenom_paginates_until_total() -> None:
    first = [{"jobSeqNo": f"S{i}", "title": f"J{i}"} for i in range(50)]
    second = [{"jobSeqNo": "S50", "title": "J50"}]
    respx.post("https://careers.acme.example/widgets").mock(
        side_effect=[
            httpx.Response(200, json=_phenom(first, 51)),
            httpx.Response(200, json=_phenom(second, 51)),
        ]
    )
    async with HttpClient(user_agent="t") as http:
        result = await PhenomPeopleConnector(http, domain="careers.acme.example").fetch_jobs()
    assert len(result.jobs) == 51
    assert result.complete is True


@respx.mock
async def test_phenom_total_greater_than_received_is_incomplete() -> None:
    respx.post("https://careers.acme.example/widgets").mock(
        side_effect=[
            httpx.Response(200, json=_phenom([{"jobSeqNo": "S1", "title": "J"}], 9)),
            httpx.Response(200, json=_phenom([], 9)),
        ]
    )
    async with HttpClient(user_agent="t") as http:
        result = await PhenomPeopleConnector(http, domain="careers.acme.example").fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False


def test_jsonld_tolerates_stray_backslash_and_raw_newline() -> None:
    from skilometer.connectors.jsonld import extract_job_postings

    html = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "T", "description": "out' + chr(92) + 'inbound\nok"}'
        "</script>"
    )
    postings = extract_job_postings(html)
    assert len(postings) == 1
    assert postings[0]["title"] == "T"
