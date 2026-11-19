"""Chromium real via Playwright (rede). Requer `uv sync --extra browser` e chromium instalado."""

import asyncio
import sys

import pytest

from vaya_indexer.core.exceptions import BrowserUnavailable
from vaya_indexer.extraction.browser import BrowserFetcher, BrowserPage

pytestmark = pytest.mark.live


async def _load() -> BrowserPage:
    async with BrowserFetcher(user_agent="VayaJobIndexer/0.1 (live-test)", timeout=30) as fetcher:
        return await fetcher.fetch_html("https://example.com/")


def _run_in_proactor_loop() -> BrowserPage:
    # Playwright precisa de subprocessos; no Windows isso exige ProactorEventLoop, enquanto a
    # suite usa SelectorEventLoop (psycopg async). Roda em thread com loop proprio.
    if sys.platform == "win32":
        loop: asyncio.AbstractEventLoop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_load())
    finally:
        loop.close()


async def test_browser_loads_public_page() -> None:
    pytest.importorskip("playwright")
    try:
        page = await asyncio.to_thread(_run_in_proactor_loop)
    except BrowserUnavailable as exc:
        pytest.skip(str(exc))
    assert page.status == 200
    assert "Example Domain" in page.html
