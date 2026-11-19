"""Utilitarios dos conectores do lote 3A (job boards): sitemap + JSON-LD e fallback por RSS.

SmartJobBoard, JobBoard.io e Niceboard expoem todas as vagas no sitemap e cada pagina de vaga
traz um `JobPosting` em JSON-LD. O RSS dessas plataformas e truncado (29 a 50 itens), entao so
serve de fallback quando o sitemap nao esta disponivel (e nesse caso `complete=False`).
"""

import asyncio
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from vaya_indexer.connectors._common import clean
from vaya_indexer.connectors._web import (
    DETAIL_CONCURRENCY,
    gather_details,
    get_bytes,
    get_text,
    rss_entries,
    sitemap_locs,
)
from vaya_indexer.connectors.base import FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.feeds import parse_xml
from vaya_indexer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.processing.normalization import parse_iso_datetime
from vaya_indexer.processing.text import html_to_text

MAX_JOBS = 300

Extra = Callable[[str], dict[str, Any]]


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host.removeprefix("www.")


async def job_pairs(
    http: HttpClient,
    sitemap_url: str,
    domain: str,
    job_re: re.Pattern[str],
    child_re: re.Pattern[str] | None = None,
) -> list[tuple[str, str]]:
    """(id, url) das vagas de um sitemap `urlset` ou de um indice (`child_re` filtra os filhos).

    So aceita URLs do proprio host do board (com ou sem `www.`). XML invalido -> ParseError.
    """
    xml = await get_bytes(http, sitemap_url)
    root = parse_xml(xml)
    locs = sitemap_locs(xml)
    if str(root.tag).rsplit("}", 1)[-1] == "sitemapindex":
        children = [c for c in locs if child_re is not None and child_re.search(c)]
        locs = []
        for child in children:
            if _host(child) == _host(f"https://{domain}/"):
                locs.extend(sitemap_locs(await get_bytes(http, child)))
    pairs: dict[str, str] = {}
    for loc in locs:
        match = job_re.match(loc)
        if match and _host(loc) == _host(f"https://{domain}/"):
            pairs.setdefault(match.group(1), loc)
    return list(pairs.items())


async def jobs_from_pairs(
    http: HttpClient,
    pairs: list[tuple[str, str]],
    *,
    extra: Extra | None = None,
    delay: float = 0.0,
    max_jobs: int = MAX_JOBS,
) -> FetchResult:
    """Le o JSON-LD de cada vaga (ate `max_jobs`). Detalhe sem JobPosting/falho -> complete=False.

    `extra(html)` devolve campos adicionais do payload (ex.: URL de candidatura). `delay` espaca
    as requisicoes (Crawl-delay do robots.txt) e forca uma requisicao por vez.
    """
    complete = len(pairs) <= max_jobs
    pairs = pairs[:max_jobs]

    async def detail(pair: tuple[str, str]) -> dict[str, Any] | None:
        if delay:
            await asyncio.sleep(delay)
        html = await get_text(http, pair[1])
        postings = extract_job_postings(html)
        if not postings:
            return None
        return {"posting": postings[0], **(extra(html) if extra else {})}

    details = await gather_details(pairs, detail, 1 if delay else DETAIL_CONCURRENCY)
    jobs: list[RawJob] = []
    for (job_id, url), found in zip(pairs, details, strict=True):
        if found is None:
            complete = False
            continue
        jobs.append(RawJob(external_id=job_id, payload={"id": job_id, "url": url, **found}))
    return FetchResult(jobs=jobs, complete=complete)


async def rss_fallback_jobs(http: HttpClient, feed_url: str, domain: str) -> FetchResult:
    """Vagas do RSS (truncado pela plataforma): sempre complete=False."""
    entries, _ = rss_entries(await get_bytes(http, feed_url))
    jobs: list[RawJob] = []
    for entry in entries:
        if not entry.link or _host(entry.link) != _host(f"https://{domain}/"):
            continue
        payload = {
            "id": entry.id,
            "url": entry.link,
            "rss": {
                "title": entry.title,
                "description": entry.summary,
                "published": entry.published.isoformat() if entry.published else None,
            },
        }
        jobs.append(RawJob(external_id=entry.id, payload=payload))
    return FetchResult(jobs=jobs, complete=False)


async def fetch_with_rss_fallback(
    http: HttpClient,
    *,
    domain: str,
    sitemap_url: str,
    feed_url: str,
    job_re: re.Pattern[str],
    child_re: re.Pattern[str] | None = None,
) -> FetchResult:
    """Sitemap + JSON-LD; se o sitemap falhar ou nao listar vagas, cai no RSS (incompleto)."""
    try:
        pairs = await job_pairs(http, sitemap_url, domain, job_re, child_re)
    except FetchError:
        pairs = []
    if pairs:
        return await jobs_from_pairs(http, pairs)
    result = await rss_fallback_jobs(http, feed_url, domain)
    if not result.jobs:
        raise ParseError(f"Nem sitemap nem RSS listaram vagas em {domain}")
    return result


def posting_to_job(payload: dict[str, Any]) -> NormalizedJob:
    """Normaliza o payload `posting` (JSON-LD) preservando URL da pagina e de candidatura."""
    posting = payload.get("posting")
    if not isinstance(posting, dict):
        raise ParseError("Vaga sem JobPosting")
    job = job_posting_to_normalized(posting)
    url = clean(payload.get("url")) or job.original_url
    apply_url = clean(payload.get("apply_url")) or url
    return job.model_copy(update={"original_url": url, "application_url": apply_url})


def rss_to_job(payload: dict[str, Any]) -> NormalizedJob:
    """Normaliza o payload de fallback RSS (so titulo, descricao, data e URL)."""
    rss = payload.get("rss")
    rss = rss if isinstance(rss, dict) else {}
    title = clean(rss.get("title"))
    if not title:
        raise ParseError("Vaga sem titulo")
    url = clean(payload.get("url"))
    description = rss.get("description")
    return NormalizedJob(
        title=title,
        description=html_to_text(description) if isinstance(description, str) else None,
        original_url=url,
        application_url=url,
        original_posted_at=parse_iso_datetime(clean(rss.get("published"))),
    )


def board_job(payload: dict[str, Any]) -> NormalizedJob:
    return posting_to_job(payload) if payload.get("posting") else rss_to_job(payload)


async def health_probe(http: HttpClient, sitemap_url: str, feed_url: str, domain: str) -> bool:
    """True se o sitemap responde como XML (ou, falhando, o RSS lista vagas)."""
    try:
        parse_xml(await get_bytes(http, sitemap_url))
    except FetchError:
        try:
            result = await rss_fallback_jobs(http, feed_url, domain)
        except FetchError:
            return False
        return bool(result.jobs)
    return True
