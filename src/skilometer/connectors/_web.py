"""Utilitarios dos conectores de feed/HTML/sitemap (lote 2B): download de paginas e sitemap."""

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from bs4 import BeautifulSoup

from skilometer.connectors.feeds import FeedEntry, parse_feed, parse_xml
from skilometer.core.exceptions import FetchError
from skilometer.extraction.http import HttpClient

DETAIL_CONCURRENCY = 4


async def get_text(http: HttpClient, url: str, **kwargs: Any) -> str:
    return (await http.request("GET", url, **kwargs)).text


async def get_bytes(http: HttpClient, url: str, **kwargs: Any) -> bytes:
    return (await http.request("GET", url, **kwargs)).content


def make_soup(html: str, parser: str = "lxml") -> BeautifulSoup:
    soup = BeautifulSoup(html, parser)
    for svg in soup.find_all("svg"):
        svg.decompose()
    return soup


def element_text(element: Any, tag: str) -> str | None:
    """Texto do primeiro filho `tag` (notacao Clark) de um elemento lxml; vazio -> None."""
    child = element.find(tag)
    if child is None:
        return None
    text = "".join(child.itertext()).strip()
    return text or None


def rss_entries(xml: bytes) -> tuple[list[FeedEntry], int]:
    """Entradas validas do RSS e quantos <item> foram descartados (sem titulo ou id)."""
    entries = parse_feed(xml)
    total = sum(1 for _ in parse_xml(xml).iter("item"))
    return entries, max(0, total - len(entries))


def sitemap_locs(xml: bytes) -> list[str]:
    """URLs (<loc>) de um sitemap `urlset`, na ordem do documento, sem repeticao."""
    root = parse_xml(xml)
    locs = [
        (el.text or "").strip()
        for el in root.iter()
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == "loc"
    ]
    return list(dict.fromkeys(loc for loc in locs if loc))


_COUNT_RE = re.compile(r"(\d[\d,]*)\s+(?:jobs?|openings?)\b", re.IGNORECASE)


def count_hint(text: str) -> int | None:
    """Primeiro 'N jobs'/'N openings' do texto (contagem declarada pela propria pagina)."""
    match = _COUNT_RE.search(text)
    return int(match.group(1).replace(",", "")) if match else None


async def gather_details[T, R](
    items: Iterable[T],
    fetch_one: Callable[[T], Awaitable[R]],
    concurrency: int = DETAIL_CONCURRENCY,
) -> list[R | None]:
    """Executa `fetch_one` com concorrencia limitada; FetchError vira None (falha isolada)."""
    semaphore = asyncio.Semaphore(concurrency)

    async def run(item: T) -> R | None:
        async with semaphore:
            try:
                return await fetch_one(item)
            except FetchError:
                return None

    return list(await asyncio.gather(*(run(i) for i in items)))
