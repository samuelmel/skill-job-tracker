"""Conectores do lote 3A (job boards) com HTTP mockado (respx)."""

import json
from typing import Any

import httpx
import pytest
import respx

import skilometer.connectors.niceboard as niceboard_mod
import skilometer.connectors.wp_job_manager as wpjm_mod
from skilometer.connectors.jobboard_io import JobBoardIoConnector
from skilometer.connectors.niceboard import NiceboardConnector
from skilometer.connectors.smartjobboard import SmartJobBoardConnector
from skilometer.connectors.wp_job_manager import WpJobManagerConnector
from skilometer.core.exceptions import ConnectorConfigError, ParseError
from skilometer.extraction.http import HttpClient

SM_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _urlset(urls: list[str]) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="{SM_NS}">{locs}</urlset>'


def _index(urls: list[str]) -> str:
    locs = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return f'<?xml version="1.0"?><sitemapindex xmlns="{SM_NS}">{locs}</sitemapindex>'


def _page(posting: dict[str, Any] | None, extra: str = "") -> str:
    ld = ""
    if posting is not None:
        body = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", **posting})
        ld = f'<script type="application/ld+json">{body}</script>'
    return f"<html>{ld}<script>{extra}</script></html>"


# -- SmartJobBoard ----------------------------------------------------------------------------


@respx.mock
async def test_smartjobboard_uses_sitemap_beyond_rss_cap_and_filters_non_jobs() -> None:
    base = "https://jobs.acme.example"
    urls = [f"{base}/job/{i}/title-{i}/" for i in range(1, 61)]
    listing = [f"{base}/", f"{base}/cities/x/", "https://other.example/job/9/x/", *urls]
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(200, text=_urlset(listing)))
    respx.get(url__regex=rf"{base}/job/\d+/").mock(
        return_value=httpx.Response(200, text=_page({"title": "T"}))
    )
    async with HttpClient(user_agent="t") as http:
        connector = SmartJobBoardConnector(http, board_domain="jobs.acme.example")
        result = await connector.fetch_jobs()
    assert len(result.jobs) == 60
    assert result.complete is True
    job = connector.normalize(result.jobs[0])
    assert job.original_url == f"{base}/job/1/title-1/"
    assert job.application_url == job.original_url


@respx.mock
async def test_smartjobboard_failed_detail_is_incomplete() -> None:
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=_urlset([f"{base}/job/1/a/", f"{base}/job/2/b/"]))
    )
    respx.get(f"{base}/job/1/a/").mock(return_value=httpx.Response(200, text=_page({"title": "A"})))
    respx.get(f"{base}/job/2/b/").mock(return_value=httpx.Response(200, text=_page(None)))
    async with HttpClient(user_agent="t") as http:
        result = await SmartJobBoardConnector(http, board_domain="jobs.acme.example").fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1"]
    assert result.complete is False


@respx.mock
async def test_smartjobboard_falls_back_to_rss_as_incomplete() -> None:
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(404))
    rss = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><title>SMO</title><link>{base}/job/7/smo/</link>"
        "<description>&lt;p&gt;Hello&lt;/p&gt;</description></item></channel></rss>"
    )
    respx.get(f"{base}/rss").mock(return_value=httpx.Response(200, text=rss))
    async with HttpClient(user_agent="t") as http:
        connector = SmartJobBoardConnector(http, board_domain="jobs.acme.example")
        result = await connector.fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False
    job = connector.normalize(result.jobs[0])
    assert job.title == "SMO"
    assert job.application_url == f"{base}/job/7/smo/"


@respx.mock
async def test_smartjobboard_invalid_sitemap_raises() -> None:
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(200, text="<html/>"))
    respx.get(f"{base}/rss").mock(return_value=httpx.Response(200, text="<html/>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await SmartJobBoardConnector(http, board_domain="jobs.acme.example").fetch_jobs()


# -- JobBoard.io ------------------------------------------------------------------------------


@respx.mock
async def test_jobboard_io_follows_index_to_job_sitemaps() -> None:
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text=_index(
                [
                    f"{base}/sitemaps/jobs/job_sitemap_details.xml",
                    f"{base}/sitemaps/pages/page_sitemap_details.xml",
                ]
            ),
        )
    )
    respx.get(f"{base}/sitemaps/jobs/job_sitemap_details.xml").mock(
        return_value=httpx.Response(
            200, text=_urlset([base, f"{base}/jobs/11-first-at-x", f"{base}/jobs/12-second-at-x"])
        )
    )
    respx.get(url__regex=rf"{base}/jobs/1\d-").mock(
        return_value=httpx.Response(200, text=_page({"title": "J", "datePosted": "2026-11-17"}))
    )
    async with HttpClient(user_agent="t") as http:
        connector = JobBoardIoConnector(http, board_domain="jobs.acme.example")
        result = await connector.fetch_jobs()
    assert sorted(j.external_id for j in result.jobs) == ["11", "12"]
    assert result.complete is True
    by_id = {j.external_id: j for j in result.jobs}
    assert connector.normalize(by_id["12"]).original_url == f"{base}/jobs/12-second-at-x"


@respx.mock
async def test_jobboard_io_rss_fallback_when_sitemap_missing() -> None:
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(404))
    rss = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><guid>{base}/jobs/5-x</guid><title>X at Y</title><link>{base}/jobs/5-x</link>"
        "<pubDate>Tue, 17 Nov 2026 22:10:52 +0000</pubDate></item></channel></rss>"
    )
    respx.get(f"{base}/jobs.rss").mock(return_value=httpx.Response(200, text=rss))
    async with HttpClient(user_agent="t") as http:
        result = await JobBoardIoConnector(http, board_domain="jobs.acme.example").fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False


# -- Niceboard --------------------------------------------------------------------------------


@respx.mock
async def test_niceboard_reads_apply_url_and_flags_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(niceboard_mod, "CRAWL_DELAY", 0.0)
    monkeypatch.setattr(niceboard_mod, "MAX_JOBS", 2)
    base = "https://jobs.acme.example"
    respx.get(f"{base}/sitemap-jobs.xml").mock(
        return_value=httpx.Response(
            200, text=_urlset([f"{base}/job/{i}-slug-{i}" for i in (1, 2, 3)])
        )
    )
    apply = 'var job_json = {"id":1,"apply_url":"https://careers.example/apply?x=1"};'
    respx.get(f"{base}/job/1-slug-1").mock(
        return_value=httpx.Response(200, text=_page({"title": "One"}, apply))
    )
    respx.get(f"{base}/job/2-slug-2").mock(
        return_value=httpx.Response(
            200, text=_page({"title": "Two"}, 'var job_json = {"apply_url":null};')
        )
    )
    async with HttpClient(user_agent="t") as http:
        connector = NiceboardConnector(http, board_domain="jobs.acme.example")
        result = await connector.fetch_jobs()
    assert [j.external_id for j in result.jobs] == ["1", "2"]
    assert result.complete is False
    one, two = (connector.normalize(j) for j in result.jobs)
    assert one.original_url == f"{base}/job/1-slug-1"
    assert one.application_url == "https://careers.example/apply?x=1"
    assert two.application_url == f"{base}/job/2-slug-2"


@respx.mock
async def test_niceboard_invalid_sitemap_raises() -> None:
    respx.get("https://jobs.acme.example/sitemap-jobs.xml").mock(
        return_value=httpx.Response(200, text="nope")
    )
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await NiceboardConnector(http, board_domain="jobs.acme.example").fetch_jobs()


# -- WP Job Manager ---------------------------------------------------------------------------


def _feed(items: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><guid>{link}</guid>"
        "<content:encoded><![CDATA[<p>Desc</p>]]></content:encoded>"
        "<job_listing:location><![CDATA[Remote]]></job_listing:location>"
        "<job_listing:company><![CDATA[Acme]]></job_listing:company>"
        "<job_listing:job_type><![CDATA[Full Time]]></job_listing:job_type></item>"
        for t, link in items
    )
    return (
        '<?xml version="1.0"?><rss version="2.0" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:job_listing="https://acme.example/sub"><channel>'
        f"{body}</channel></rss>"
    )


@respx.mock
async def test_wpjm_paginates_until_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wpjm_mod, "PAGE_SIZE", 2)
    route = respx.get("https://acme.example/sub/")

    def pages(request: httpx.Request) -> httpx.Response:
        page = request.url.params["paged"]
        data = {
            "1": [
                ("A", "https://acme.example/sub/job/a/"),
                ("B", "https://acme.example/sub/job/b/"),
            ],
            "2": [("C", "https://acme.example/sub/job/c/")],
        }.get(page, [])
        return httpx.Response(200, text=_feed(data))

    route.mock(side_effect=pages)
    async with HttpClient(user_agent="t") as http:
        connector = WpJobManagerConnector(http, base_url="https://acme.example/sub/")
        result = await connector.fetch_jobs()
    assert len(result.jobs) == 3
    assert result.complete is True
    assert route.calls[0].request.url.params["feed"] == "job_feed"
    job = connector.normalize(result.jobs[0])
    assert job.company_name == "Acme"
    assert job.employment_type == "FULL_TIME"
    assert job.application_url == "https://acme.example/sub/job/a/"


@respx.mock
async def test_wpjm_site_ignoring_paged_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wpjm_mod, "PAGE_SIZE", 2)
    same = _feed([("A", "https://acme.example/job/a/"), ("B", "https://acme.example/job/b/")])
    respx.get("https://acme.example/").mock(return_value=httpx.Response(200, text=same))
    async with HttpClient(user_agent="t") as http:
        result = await WpJobManagerConnector(http, base_url="https://acme.example").fetch_jobs()
    assert len(result.jobs) == 2
    assert result.complete is False


@respx.mock
async def test_wpjm_later_page_error_keeps_jobs_but_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wpjm_mod, "PAGE_SIZE", 1)

    def pages(request: httpx.Request) -> httpx.Response:
        if request.url.params["paged"] == "1":
            return httpx.Response(200, text=_feed([("A", "https://acme.example/job/a/")]))
        return httpx.Response(404)

    respx.get("https://acme.example/").mock(side_effect=pages)
    async with HttpClient(user_agent="t") as http:
        result = await WpJobManagerConnector(http, base_url="https://acme.example").fetch_jobs()
    assert len(result.jobs) == 1
    assert result.complete is False


@respx.mock
async def test_wpjm_first_page_not_rss_raises_parse_error() -> None:
    respx.get("https://acme.example/").mock(return_value=httpx.Response(200, text="<html/>"))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await WpJobManagerConnector(http, base_url="https://acme.example").fetch_jobs()


# -- config -----------------------------------------------------------------------------------

BAD_CONFIGS: list[tuple[Any, dict[str, Any]]] = [
    (WpJobManagerConnector, {"base_url": "http://acme.example"}),
    (WpJobManagerConnector, {"base_url": "https://acme.example/?x=1"}),
    (WpJobManagerConnector, {"base_url": "https://user@acme.example"}),
    (WpJobManagerConnector, {"base_url": "https://acme.example:8443"}),
    (NiceboardConnector, {"board_domain": "acme.example/path"}),
    (SmartJobBoardConnector, {"board_domain": "https://acme.example"}),
    (JobBoardIoConnector, {"board_domain": ""}),
]


@pytest.mark.parametrize(("cls", "config"), BAD_CONFIGS)
def test_invalid_config_is_rejected(cls: Any, config: dict[str, Any]) -> None:
    with pytest.raises(ConnectorConfigError):
        cls.validate_config(config)


def test_wpjm_base_url_is_normalized() -> None:
    config = WpJobManagerConnector.validate_config({"base_url": "https://Acme.example/sub/"})
    assert WpJobManagerConnector.external_id_from_config(config) == "acme.example/sub"
