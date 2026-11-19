"""Testes mockados (respx) dos conectores do lote 1: parse, paginacao e complete=False."""

import httpx
import pytest
import respx

from vaya_indexer.connectors.ashby import AshbyConnector
from vaya_indexer.connectors.bamboohr import BambooConnector
from vaya_indexer.connectors.breezy_hr import BreezyConnector
from vaya_indexer.connectors.gem_com import GemConnector
from vaya_indexer.connectors.hirehive import HireHiveConnector
from vaya_indexer.connectors.lever_co import LeverConnector
from vaya_indexer.connectors.manatal import ManatalConnector
from vaya_indexer.connectors.pinpoint import PinpointConnector
from vaya_indexer.connectors.polymer import PolymerConnector
from vaya_indexer.connectors.recruitee import RecruiteeConnector
from vaya_indexer.connectors.rippling import RipplingConnector
from vaya_indexer.connectors.workable import WorkableConnector
from vaya_indexer.connectors.workday import WorkdayConnector
from vaya_indexer.core.exceptions import ParseError
from vaya_indexer.extraction.http import HttpClient

CASES = [
    (AshbyConnector, {"org_slug": "acme"}, "https://api.ashbyhq.com/posting-api/job-board/acme"),
    (LeverConnector, {"company": "acme"}, "https://api.lever.co/v0/postings/acme"),
    (RecruiteeConnector, {"company": "acme"}, "https://acme.recruitee.com/api/offers/"),
    (GemConnector, {"board_slug": "acme"}, "https://api.gem.com/job_board/v0/acme/job_posts/"),
    (PinpointConnector, {"subdomain": "acme"}, "https://acme.pinpointhq.com/postings.json"),
    (BreezyConnector, {"company": "acme"}, "https://acme.breezy.hr/json"),
    (BambooConnector, {"subdomain": "acme"}, "https://acme.bamboohr.com/careers/list"),
    (
        RipplingConnector,
        {"board": "acme"},
        "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs",
    ),
    (PolymerConnector, {"org": "acme"}, "https://api.polymer.co/v1/hire/organizations/acme/jobs"),
    (HireHiveConnector, {"subdomain": "acme"}, "https://acme.hirehive.com/api/v2/jobs"),
    (ManatalConnector, {"slug": "acme"}, "https://api.manatal.com/open/v3/career-page/acme/jobs/"),
]


@pytest.mark.parametrize(("cls", "config", "url"), CASES)
@respx.mock
async def test_unexpected_shape_raises_parse_error(cls, config, url) -> None:  # type: ignore[no-untyped-def]
    respx.route(url__startswith=url).mock(return_value=httpx.Response(200, json={"foo": 1}))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await cls(http, **config).fetch_jobs()


@respx.mock
async def test_ashby_filters_unlisted_and_flags_missing_id() -> None:
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    jobs = [
        {"id": "1", "title": "A", "isListed": True},
        {"id": "2", "title": "B", "isListed": False},
    ]
    respx.get(url).mock(return_value=httpx.Response(200, json={"jobs": jobs}))
    async with HttpClient(user_agent="t") as http:
        result = await AshbyConnector(http, org_slug="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is True
    respx.get(url).mock(return_value=httpx.Response(200, json={"jobs": [{"title": "sem id"}]}))
    async with HttpClient(user_agent="t") as http:
        result = await AshbyConnector(http, org_slug="acme").fetch_jobs()
    assert result.jobs == []
    assert result.complete is False


@respx.mock
async def test_lever_eu_region_and_full_listing() -> None:
    route = respx.get("https://api.eu.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=[{"id": "x", "text": "T"}])
    )
    async with HttpClient(user_agent="t") as http:
        connector = LeverConnector(http, company="acme", region="eu")
        result = await connector.fetch_jobs()
    assert route.calls[0].request.url.params["mode"] == "json"
    assert [j.external_id for j in result.jobs] == ["x"]
    assert result.complete is True
    assert LeverConnector.external_id_from_config(connector.config) == "eu:acme"


@respx.mock
async def test_rippling_item_without_uuid_marks_incomplete() -> None:
    url = "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs"
    respx.get(url).mock(return_value=httpx.Response(200, json=[{"uuid": "u", "name": "N"}, {}]))
    async with HttpClient(user_agent="t") as http:
        result = await RipplingConnector(http, board="acme").fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False


@respx.mock
async def test_bamboohr_total_count_mismatch_is_incomplete() -> None:
    url = "https://acme.bamboohr.com/careers/list"
    body = {"meta": {"totalCount": 5}, "result": [{"id": "1", "jobOpeningName": "A"}]}
    respx.get(url).mock(return_value=httpx.Response(200, json=body))
    async with HttpClient(user_agent="t") as http:
        result = await BambooConnector(http, subdomain="acme").fetch_jobs()
    assert result.complete is False
    body["meta"]["totalCount"] = 1
    respx.get(url).mock(return_value=httpx.Response(200, json=body))
    async with HttpClient(user_agent="t") as http:
        result = await BambooConnector(http, subdomain="acme").fetch_jobs()
    assert result.complete is True


@respx.mock
async def test_workday_post_offset_pagination() -> None:
    url = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/jobs"

    def posting(i: int) -> dict[str, object]:
        return {"title": f"J{i}", "externalPath": f"/job/X/J{i}"}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        offset = json.loads(request.content)["offset"]
        total = 25 if offset == 0 else 0  # Workday so informa total na primeira pagina
        page = [posting(i) for i in range(offset, min(offset + 20, 25))]
        return httpx.Response(200, json={"total": total, "jobPostings": page})

    route = respx.post(url).mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await WorkdayConnector(http, tenant="acme", host="wd5", site="Site").fetch_jobs()
    assert len(result.jobs) == 25
    assert result.complete is True
    assert route.call_count == 2


@respx.mock
async def test_workday_total_mismatch_is_incomplete() -> None:
    url = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Site/jobs"
    page = {"total": 99, "jobPostings": [{"title": "J", "externalPath": "/job/X/J"}]}
    respx.post(url).mock(return_value=httpx.Response(200, json=page))
    async with HttpClient(user_agent="t") as http:
        result = await WorkdayConnector(http, tenant="acme", host="wd5", site="Site").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_workable_cursor_pagination_and_total() -> None:
    url = "https://apply.workable.com/api/v3/accounts/acme/jobs"
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body.get("token"):
            res = {"total": 2, "results": [{"shortcode": "A", "title": "a"}], "nextPage": "tok"}
        else:
            assert body["token"] == "tok"
            res = {"total": 2, "results": [{"shortcode": "B", "title": "b"}]}
        return httpx.Response(200, json=res)

    respx.post(url).mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await WorkableConnector(http, account="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["A", "B"]
    assert result.complete is True


@respx.mock
async def test_workable_total_mismatch_is_incomplete() -> None:
    url = "https://apply.workable.com/api/v3/accounts/acme/jobs"
    res = {"total": 9, "results": [{"shortcode": "A", "title": "a"}]}
    respx.post(url).mock(return_value=httpx.Response(200, json=res))
    async with HttpClient(user_agent="t") as http:
        result = await WorkableConnector(http, account="acme").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_polymer_pagination() -> None:
    url = "https://api.polymer.co/v1/hire/organizations/acme/jobs"

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            meta = {"is_last": False, "next_page": 2}
        else:
            meta = {"is_last": True, "next_page": None}
        return httpx.Response(
            200, json={"items": [{"id": page, "title": f"J{page}"}], "meta": meta}
        )

    respx.get(url).mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await PolymerConnector(http, org="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1", "2"]
    assert result.complete is True


@respx.mock
async def test_hirehive_pagination_and_total_mismatch() -> None:
    url = "https://acme.hirehive.com/api/v2/jobs"

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        meta = {"has_next_page": page == 1, "total_items": 2}
        return httpx.Response(200, json={"items": [{"id": f"j{page}"}], "meta": meta})

    respx.get(url).mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await HireHiveConnector(http, subdomain="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["j1", "j2"]
    assert result.complete is True
    respx.get(url).mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "j1"}], "meta": {"has_next_page": False, "total_items": 7}}
        )
    )
    async with HttpClient(user_agent="t") as http:
        result = await HireHiveConnector(http, subdomain="acme").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_manatal_pagination_ignores_next_host() -> None:
    url = "https://api.manatal.com/open/v3/career-page/acme/jobs/"

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        nxt = "https://core.api.manatal.com/x?page=2" if page == 1 else None
        body = {"count": 2, "next": nxt, "results": [{"id": page, "position_name": "P"}]}
        return httpx.Response(200, json=body)

    route = respx.get(url).mock(side_effect=handler)
    async with HttpClient(user_agent="t") as http:
        result = await ManatalConnector(http, slug="acme").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1", "2"]
    assert result.complete is True
    assert route.call_count == 2


@respx.mock
async def test_manatal_count_mismatch_is_incomplete() -> None:
    url = "https://api.manatal.com/open/v3/career-page/acme/jobs/"
    body = {"count": 5, "next": None, "results": [{"id": 1, "position_name": "P"}]}
    respx.get(url).mock(return_value=httpx.Response(200, json=body))
    async with HttpClient(user_agent="t") as http:
        result = await ManatalConnector(http, slug="acme").fetch_jobs()
    assert result.complete is False


@respx.mock
async def test_list_endpoints_return_jobs() -> None:
    respx.get("https://acme.breezy.hr/json").mock(
        return_value=httpx.Response(200, json=[{"id": "b1", "name": "N"}])
    )
    respx.get("https://api.gem.com/job_board/v0/acme/job_posts/").mock(
        return_value=httpx.Response(200, json=[{"id": "g1", "title": "T"}])
    )
    respx.get("https://acme.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json={"offers": [{"id": 1, "title": "T"}]})
    )
    respx.get("https://acme.pinpointhq.com/postings.json").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "p1", "title": "T"}]})
    )
    async with HttpClient(user_agent="t") as http:
        for cls, cfg in (
            (BreezyConnector, {"company": "acme"}),
            (GemConnector, {"board_slug": "acme"}),
            (RecruiteeConnector, {"company": "acme"}),
            (PinpointConnector, {"subdomain": "acme"}),
        ):
            result = await cls(http, **cfg).fetch_jobs()
            assert len(result.jobs) == 1
            assert result.complete is True


@pytest.mark.parametrize(
    ("cls", "config"),
    [
        (AshbyConnector, {"org_slug": "../evil"}),
        (RecruiteeConnector, {"company": "a.b"}),
        (WorkdayConnector, {"tenant": "acme", "host": "evil", "site": "S"}),
    ],
)
def test_config_rejects_unsafe_values(cls, config) -> None:  # type: ignore[no-untyped-def]
    from vaya_indexer.core.exceptions import ConnectorConfigError

    with pytest.raises(ConnectorConfigError):
        cls.validate_config(config)
