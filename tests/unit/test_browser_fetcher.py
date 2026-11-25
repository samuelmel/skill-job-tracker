import builtins
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import _REGISTRY, create_connector, register
from skilometer.core.exceptions import BrowserUnavailable, FetchError
from skilometer.extraction.browser import BrowserFetcher
from skilometer.extraction.http import HttpClient


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    def __init__(self, log: list[str], status: int, fail: bool) -> None:
        self.log, self.status, self.fail = log, status, fail
        self.url = "https://exemplo.test/final"

    async def goto(self, url: str, wait_until: str, timeout: float) -> FakeResponse:  # noqa: ASYNC109 (espelha a API do Playwright)
        self.log.append(f"goto {url} {wait_until} {timeout}")
        if self.fail:
            raise TimeoutError("lento")
        return FakeResponse(self.status)

    async def wait_for_selector(self, selector: str, timeout: float) -> None:  # noqa: ASYNC109
        self.log.append(f"wait {selector}")

    async def content(self) -> str:
        return "<html>ok</html>"

    async def close(self) -> None:
        self.log.append("page.close")


class FakeContext:
    def __init__(self, log: list[str], status: int, fail: bool) -> None:
        self.log, self.status, self.fail = log, status, fail

    async def new_page(self) -> FakePage:
        return FakePage(self.log, self.status, self.fail)

    async def close(self) -> None:
        self.log.append("context.close")


class FakeBrowser:
    def __init__(self, log: list[str], status: int, fail: bool) -> None:
        self.log, self.status, self.fail = log, status, fail

    async def new_context(self, user_agent: str) -> FakeContext:
        self.log.append(f"ua {user_agent}")
        return FakeContext(self.log, self.status, self.fail)

    async def close(self) -> None:
        self.log.append("browser.close")


class FakeChromium:
    def __init__(self, log: list[str], status: int, fail: bool, broken: bool) -> None:
        self.args = (log, status, fail)
        self.broken, self.log = broken, log

    async def launch(self, headless: bool) -> FakeBrowser:
        if self.broken:
            raise RuntimeError("Executable missing")
        self.log.append(f"launch headless={headless}")
        return FakeBrowser(*self.args)


class FakePlaywright:
    def __init__(self, log: list[str], status: int, fail: bool, broken: bool) -> None:
        self.chromium = FakeChromium(log, status, fail, broken)
        self.log = log

    async def stop(self) -> None:
        self.log.append("playwright.stop")


class FakeDriver:
    def __init__(self, pw: FakePlaywright) -> None:
        self.pw = pw

    async def start(self) -> FakePlaywright:
        return self.pw


def make(
    log: list[str], status: int = 200, fail: bool = False, broken: bool = False
) -> BrowserFetcher:
    return BrowserFetcher(
        user_agent="Skilometer-Test/1.0",
        timeout=5,
        playwright_factory=lambda: FakeDriver(FakePlaywright(log, status, fail, broken)),
    )


async def test_fetch_html_and_closes_resources_in_order() -> None:
    log: list[str] = []
    async with make(log) as fetcher:
        page = await fetcher.fetch_html("https://exemplo.test/", wait_for_selector="#jobs")
    assert page.html == "<html>ok</html>" and page.status == 200
    assert page.url == "https://exemplo.test/final"
    assert "ua Skilometer-Test/1.0" in log and "launch headless=True" in log
    assert "goto https://exemplo.test/ domcontentloaded 5000" in log and "wait #jobs" in log
    assert log[-3:] == ["context.close", "browser.close", "playwright.stop"]


async def test_launch_is_lazy_and_reused() -> None:
    log: list[str] = []
    fetcher = make(log)
    await fetcher.aclose()  # sem uso: nada a fechar
    assert log == []
    await fetcher.fetch_html("https://exemplo.test/a")
    await fetcher.fetch_html("https://exemplo.test/b")
    assert log.count("launch headless=True") == 1
    await fetcher.aclose()
    await fetcher.aclose()  # idempotente
    assert log.count("browser.close") == 1


async def test_http_error_and_navigation_failure_are_fetch_errors() -> None:
    log: list[str] = []
    async with make(log, status=503) as fetcher:
        with pytest.raises(FetchError, match="HTTP 503"):
            await fetcher.fetch_html("https://exemplo.test/")
    assert "page.close" in log
    async with make([], fail=True) as fetcher:
        with pytest.raises(FetchError, match="TimeoutError"):
            await fetcher.fetch_html("https://exemplo.test/")


async def test_missing_chromium_raises_typed_error_and_cleans_up() -> None:
    log: list[str] = []
    fetcher = make(log, broken=True)
    with pytest.raises(BrowserUnavailable, match="playwright install chromium"):
        await fetcher.fetch_html("https://exemplo.test/")
    assert log == ["playwright.stop"]


async def test_missing_playwright_package(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("playwright"):
            raise ImportError("sem playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BrowserUnavailable, match="uv sync --extra browser"):
        await BrowserFetcher().fetch_html("https://exemplo.test/")


async def test_browser_connector_requires_fetcher() -> None:
    class Cfg(BaseModel):
        x: str = "a"

    @register("fake-browser")
    class FakeBrowserConnector(BaseConnector):
        config_model: ClassVar[type[BaseModel]] = Cfg
        requires_browser = True

        @classmethod
        def external_id_from_config(cls, config: BaseModel) -> str:
            return "x"

        async def fetch_jobs(self) -> FetchResult:
            return FetchResult(jobs=[], complete=True)

        def normalize(self, raw: RawJob) -> NormalizedJob:
            raise NotImplementedError

        async def health_check(self) -> bool:
            return True

    try:
        async with HttpClient(user_agent="t") as http:
            with pytest.raises(BrowserUnavailable):
                create_connector("fake-browser", {}, http)
            fetcher = make([])
            connector = create_connector("fake-browser", {}, http, fetcher)
            assert connector.browser is fetcher
    finally:
        _REGISTRY.pop("fake-browser", None)
