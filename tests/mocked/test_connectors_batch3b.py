"""Testes mockados (respx) dos conectores do lote 3b: parse, paginacao e complete=False."""

import json
from typing import Any

import httpx
import pytest
import respx

from vaya_indexer.connectors import jboard_io, jobboardhq, jobiqo
from vaya_indexer.connectors.jboard_io import JBoardIoConnector
from vaya_indexer.connectors.jobboardhq import JobBoardHQConnector
from vaya_indexer.connectors.jobboardly import JobboardlyConnector
from vaya_indexer.connectors.jobiqo import JobiqoConnector
from vaya_indexer.core.exceptions import ConnectorConfigError, ParseError
from vaya_indexer.extraction.http import HttpClient

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _jsonld(title: str = "Job", **extra: Any) -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": "<p>Text</p>",
        "datePosted": "2026-11-01",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        **extra,
    }
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'


def _urlset(items: list[tuple[str, str | None]]) -> str:
    body = "".join(
        f"<url><loc>{loc}</loc>" + (f"<lastmod>{mod}</lastmod>" if mod else "") + "</url>"
        for loc, mod in items
    )
    return f'<?xml version="1.0"?><urlset xmlns="{SITEMAP_NS}">{body}</urlset>'


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jboard_io, "DELAY", 0)
    monkeypatch.setattr(jobiqo, "DELAY", 0)


# -- formato inesperado -> ParseError -------------------------------------------------------

SHAPE_CASES = [
    (JobboardlyConnector, "https://b.example/jobs.rss", "<html/>"),
    (JBoardIoConnector, "https://b.example/sitemap.xml", "x"),
    (JobiqoConnector, "https://b.example/sitemap.xml", "x"),
    (JobBoardHQConnector, "https://b.example/search", "<html><body>nada</body></html>"),
]


@pytest.mark.parametrize(("cls", "url", "body"), SHAPE_CASES)
@respx.mock
async def test_unexpected_shape_raises_parse_error(cls, url, body) -> None:  # type: ignore[no-untyped-def]
    respx.get(url).mock(return_value=httpx.Response(200, text=body))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await cls(http, board_domain="b.example").fetch_jobs()


@pytest.mark.parametrize(
    "cls", [JobboardlyConnector, JBoardIoConnector, JobiqoConnector, JobBoardHQConnector]
)
def test_config_rejects_unsafe_host(cls) -> None:  # type: ignore[no-untyped-def]
    for bad in ("evil.com/path", "a b", "x@y.com", ""):
        with pytest.raises(ConnectorConfigError):
            cls.validate_config({"board_domain": bad})


# -- Jobboardly ----------------------------------------------------------------------------

RSS_NS = 'xmlns:job="https://www.jobboardly.com/rss/job"'


def _item(guid: str, title: str, city: str = "Exeter") -> str:
    return (
        f"<item><title>{title}</title><link>https://b.example/jobs/{guid}</link>"
        f"<description><![CDATA[<p>Desc</p>]]></description><guid>{guid}</guid>"
        f"<pubDate>Tue, 10 Nov 2026 09:00:00 +0000</pubDate>"
        f"<job:city>{city}</job:city><job:country>UK</job:country></item>"
    )


@respx.mock
async def test_jobboardly_parses_feed() -> None:
    feed = (
        f'<?xml version="1.0"?><rss version="2.0" {RSS_NS}><channel>'
        + _item("1-a", "Dev - Acme - Exeter, UK")
        + _item("2-b", "Ops - Beta - Leeds")
        + "</channel></rss>"
    )
    respx.get("https://b.example/jobs.rss").mock(return_value=httpx.Response(200, text=feed))
    async with HttpClient(user_agent="t") as http:
        conn = JobboardlyConnector(http, board_domain="b.example")
        result = await conn.fetch_jobs()
    assert result.complete is True
    assert [j.external_id for j in result.jobs] == ["1-a", "2-b"]
    job = conn.normalize(result.jobs[0])
    assert (job.title, job.company_name) == ("Dev", "Acme")
    assert job.location == "Exeter, UK"
    assert job.original_url == job.application_url == "https://b.example/jobs/1-a"
    assert job.original_posted_at is not None


@respx.mock
async def test_jobboardly_item_without_guid_marks_incomplete() -> None:
    broken = "<item><title>No id - X - Y</title></item>"
    items = _item("1-a", "A - B - C") + broken
    feed = f'<?xml version="1.0"?><rss version="2.0" {RSS_NS}><channel>{items}</channel></rss>'
    respx.get("https://b.example/jobs.rss").mock(return_value=httpx.Response(200, text=feed))
    async with HttpClient(user_agent="t") as http:
        result = await JobboardlyConnector(http, board_domain="b.example").fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False


# -- JBoard.io -----------------------------------------------------------------------------


def _mock_jboard(shards: dict[str, list[int]]) -> None:
    index = "".join(f"<sitemap><loc>https://b.example/{n}</loc></sitemap>" for n in shards)
    index_xml = f'<?xml version="1.0"?><sitemapindex xmlns="{SITEMAP_NS}">{index}</sitemapindex>'
    respx.get("https://b.example/sitemap.xml").mock(
        return_value=httpx.Response(200, text=index_xml)
    )
    for name, ids in shards.items():
        urls = [(f"https://b.example/jobs/{i}-job-{i}", None) for i in ids]
        respx.get(f"https://b.example/{name}").mock(
            return_value=httpx.Response(200, text=_urlset(urls))
        )


@respx.mock
async def test_jboard_io_reads_shards_and_details() -> None:
    _mock_jboard({"sitemap-jobs-1.xml": [1, 2], "sitemap-jobs-2.xml": [3]})
    for i in (1, 2, 3):
        respx.get(f"https://b.example/jobs/{i}-job-{i}").mock(
            return_value=httpx.Response(200, text=_jsonld(f"Job {i}"))
        )
    async with HttpClient(user_agent="t") as http:
        conn = JBoardIoConnector(http, board_domain="b.example")
        result = await conn.fetch_jobs()
    assert result.complete is True
    assert [j.external_id for j in result.jobs] == ["3", "2", "1"]  # mais novas primeiro
    job = conn.normalize(result.jobs[0])
    assert job.title == "Job 3"
    assert job.original_url == "https://b.example/jobs/3-job-3"
    assert job.application_url == "https://b.example/jobs/3/apply"


@respx.mock
async def test_jboard_io_ignores_other_hosts_and_flags_failed_detail() -> None:
    sitemap = _urlset(
        [
            ("https://www.b.example/jobs/1-a", None),
            ("https://b.example/jobs/2-b", None),
            ("https://evil.example/jobs/3-c", None),
            ("https://b.example/about", None),
        ]
    )
    respx.get("https://b.example/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    respx.get("https://www.b.example/jobs/1-a").mock(
        return_value=httpx.Response(200, text=_jsonld())
    )
    respx.get("https://b.example/jobs/2-b").mock(return_value=httpx.Response(403))
    async with HttpClient(user_agent="t") as http:
        result = await JBoardIoConnector(http, board_domain="b.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False


@respx.mock
@pytest.mark.parametrize("status", [404, 410])
async def test_jboard_io_removed_detail_keeps_listing_complete(status: int) -> None:
    # vaga listada no sitemap mas ja retirada: nao e truncamento da listagem
    _mock_jboard({"sitemap-jobs-1.xml": [1, 2]})
    respx.get("https://b.example/jobs/1-job-1").mock(return_value=httpx.Response(status))
    respx.get("https://b.example/jobs/2-job-2").mock(
        return_value=httpx.Response(200, text=_jsonld())
    )
    async with HttpClient(user_agent="t") as http:
        result = await JBoardIoConnector(http, board_domain="b.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["2"]
    assert result.complete is True


@respx.mock
async def test_jboard_io_detail_without_jsonld_marks_incomplete() -> None:
    _mock_jboard({"sitemap-jobs-1.xml": [1]})
    respx.get("https://b.example/jobs/1-job-1").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    async with HttpClient(user_agent="t") as http:
        result = await JBoardIoConnector(http, board_domain="b.example").fetch_jobs()
    assert result.jobs == []
    assert result.complete is False


@respx.mock
async def test_jboard_io_cap_marks_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jboard_io, "MAX_JOBS", 2)
    _mock_jboard({"sitemap-jobs-1.xml": [1, 2, 3, 4]})
    for i in (3, 4):
        respx.get(f"https://b.example/jobs/{i}-job-{i}").mock(
            return_value=httpx.Response(200, text=_jsonld())
        )
    async with HttpClient(user_agent="t") as http:
        result = await JBoardIoConnector(http, board_domain="b.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["4", "3"]
    assert result.complete is False


# -- Jobiqo --------------------------------------------------------------------------------


@respx.mock
async def test_jobiqo_filters_job_urls_newest_first_and_unescapes() -> None:
    sitemap = _urlset(
        [
            ("https://b.example/", "2026-11-19"),
            ("https://b.example/organization/acme", "2026-11-19"),
            ("https://b.example/job/old-100", "2026-10-01"),
            ("https://b.example/job/new-200", "2026-11-18"),
        ]
    )
    respx.get("https://b.example/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    escaped = "&lt;h3&gt;Role&lt;/h3&gt;&lt;p&gt;Hello&lt;/p&gt;"
    respx.get("https://b.example/job/new-200").mock(
        return_value=httpx.Response(200, text=_jsonld("New &amp; Co", description=escaped))
    )
    respx.get("https://b.example/job/old-100").mock(
        return_value=httpx.Response(200, text=_jsonld("Old"))
    )
    async with HttpClient(user_agent="t") as http:
        conn = JobiqoConnector(http, board_domain="b.example")
        result = await conn.fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["200", "100"]
    assert result.complete is True
    job = conn.normalize(result.jobs[0])
    assert job.title == "New & Co"
    assert job.description is not None
    assert "<" not in job.description
    assert "Hello" in job.description
    assert job.original_url == job.application_url == "https://b.example/job/new-200"


@respx.mock
async def test_jobiqo_cap_and_missing_jsonld_mark_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobiqo, "MAX_JOBS", 2)
    sitemap = _urlset([(f"https://b.example/job/j-{i}", f"2026-11-{10 + i}") for i in range(1, 5)])
    respx.get("https://b.example/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    respx.get("https://b.example/job/j-4").mock(return_value=httpx.Response(200, text=_jsonld()))
    respx.get("https://b.example/job/j-3").mock(return_value=httpx.Response(200, text="<html/>"))
    async with HttpClient(user_agent="t") as http:
        result = await JobiqoConnector(http, board_domain="b.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["4"]
    assert result.complete is False


# -- jobBoardHQ ----------------------------------------------------------------------------

ORIGIN = "https://www.b.example"


def _card(i: str, company: str = "Acme") -> str:
    return (
        f'<div class="listRow"><a id="lnkJobId" href="/job/{i}/title/City/NJ">Title {i}</a>'
        f'<div id="companyLine"><span>{company}</span><span>-</span><span>City, NJ</span>'
        "<span>-</span><span>11/18/2026</span></div></div>"
    )


def _page(ids: list[str], current: int, last: int, note: str = "") -> str:
    links = "".join(
        f"""<a href="javascript:__doPostBack('Page','{n}')">{n}</a>"""
        for n in range(1, last + 1)
        if n != current
    )
    return (
        '<html><body><form id="form1">'
        f'<input type="hidden" name="__VIEWSTATE" value="vs{current}"/>'
        f'<input type="hidden" name="__EVENTTARGET" value=""/>{note}'
        f'<span class="pagerCurrentPage">{current}</span>{links}'
        + "".join(_card(i) for i in ids)
        + "</form></body></html>"
    )


PAGES = {1: ["a1", "a2"], 2: ["b1", "b2"], 3: ["c1"]}


def _mock_hq(stale_first_post: bool) -> list[dict[str, str]]:
    posts: list[dict[str, str]] = []
    respx.get("https://b.example/search").mock(
        return_value=httpx.Response(301, headers={"location": f"{ORIGIN}/search"})
    )
    respx.get(f"{ORIGIN}/search").mock(return_value=httpx.Response(200, text=_page(PAGES[1], 1, 3)))

    def post(request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        posts.append(form)
        wanted = int(form["__EVENTARGUMENT"])
        if stale_first_post and len(posts) == 1:
            return httpx.Response(200, text=_page(PAGES[1], 1, 3))
        return httpx.Response(200, text=_page(PAGES[wanted], wanted, 3))

    respx.post(f"{ORIGIN}/search").mock(side_effect=post)
    for ids in PAGES.values():
        for i in ids:
            respx.get(f"{ORIGIN}/job/{i}/title/City/NJ").mock(
                return_value=httpx.Response(200, text=_jsonld(f"Title {i}"))
            )
    return posts


@respx.mock
async def test_jobboardhq_paginates_by_postback_to_final_url_with_retry() -> None:
    posts = _mock_hq(stale_first_post=True)
    async with HttpClient(user_agent="t") as http:
        conn = JobBoardHQConnector(http, board_domain="b.example")
        result = await conn.fetch_jobs()
    assert sorted(j.external_id for j in result.jobs) == ["a1", "a2", "b1", "b2", "c1"]
    assert result.complete is True
    assert posts[0]["__EVENTTARGET"] == "Page"
    assert posts[0]["__VIEWSTATE"] == "vs1"
    assert posts[1]["__EVENTARGUMENT"] == "2"  # repetiu a pagina 2 apos resposta velha
    job = conn.normalize(next(j for j in result.jobs if j.external_id == "b1"))
    assert job.original_url == f"{ORIGIN}/job/b1/title/City/NJ"
    assert job.application_url == job.original_url
    assert job.company_name == "Acme"


@respx.mock
async def test_jobboardhq_page_never_reached_marks_incomplete() -> None:
    page_one = _page(PAGES[1], 1, 2)
    respx.get("https://b.example/search").mock(return_value=httpx.Response(200, text=page_one))
    respx.post("https://b.example/search").mock(
        return_value=httpx.Response(200, text=page_one)  # sempre a pagina 1
    )
    for i in PAGES[1]:
        respx.get(f"https://b.example/job/{i}/title/City/NJ").mock(
            return_value=httpx.Response(200, text=_jsonld())
        )
    async with HttpClient(user_agent="t") as http:
        result = await JobBoardHQConnector(http, board_domain="b.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["a1", "a2"]
    assert result.complete is False


@respx.mock
async def test_jobboardhq_declared_count_multiple_of_500_means_server_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobboardhq, "MAX_JOBS", 1000)
    ids = [f"j{n}" for n in range(500)]
    page = _page(ids, 1, 1, "<p>500 jobs found</p>")
    respx.get("https://b.example/search").mock(return_value=httpx.Response(200, text=page))
    respx.get(url__regex=r"https://b\.example/job/.*").mock(
        return_value=httpx.Response(200, text=_jsonld())
    )
    async with HttpClient(user_agent="t") as http:
        result = await JobBoardHQConnector(http, board_domain="b.example").fetch_jobs()
    assert len(result.jobs) == 500
    assert result.complete is False


@respx.mock
async def test_jobboardhq_cap_and_failed_detail_mark_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobboardhq, "MAX_JOBS", 2)
    _mock_hq(stale_first_post=False)
    respx.get(f"{ORIGIN}/job/a2/title/City/NJ").mock(return_value=httpx.Response(404))
    async with HttpClient(user_agent="t") as http:
        conn = JobBoardHQConnector(http, board_domain="b.example")
        result = await conn.fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["a1", "a2"]
    assert result.complete is False
    # detalhe ausente: normaliza so com o cartao (data MM/DD/YYYY, empresa do cartao)
    job = conn.normalize(result.jobs[1])
    assert job.title == "Title a2"
    assert job.original_posted_at is not None
    assert job.original_posted_at.year == 2026
