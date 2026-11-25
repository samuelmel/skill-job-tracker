"""Conector Jobiqo (Drupal: Simple XML Sitemap + JSON-LD JobPosting das paginas de vaga).

`/sitemap.xml` e um urlset plano com paginas diversas; as vagas sao `/job/{slug}-{id}` (id
numerico final), cada uma com `<lastmod>`. O robots.txt dos quadros testados (miamijobs.com,
ruhr24jobs.de) pede `Crawl-delay: 10`: as paginas sao lidas em sequencia com `DELAY` entre elas e
so as `MAX_JOBS` com lastmod mais recente entram; se houver mais, `complete=False`. O JSON-LD traz
a descricao com o HTML escapado (`&lt;h3&gt;`) e nao traz `url`; o URL da vaga e o do sitemap e,
como a pagina nao expoe um link de candidatura estavel, tambem serve de URL de candidatura.
"""

import asyncio
import html
import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import HOST_PATTERN
from skilometer.connectors._web import get_bytes, get_text
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.feeds import parse_xml
from skilometer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

MAX_JOBS = 200
DELAY = 10.0  # segundos entre paginas (Crawl-delay: 10 do robots.txt)
_JOB_PATH = re.compile(r"^/job/[^/]*?-(\d+)/?$")


class JobiqoConfig(BaseModel):
    board_domain: str = Field(min_length=1, pattern=HOST_PATTERN)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def sitemap_jobs(xml: bytes) -> list[tuple[str, str, str]]:
    """[(id, url, lastmod)] das paginas /job/... do sitemap, mais recentes primeiro."""
    root = parse_xml(xml)
    found: dict[str, tuple[str, str]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.rsplit("}", 1)[-1] != "url":
            continue
        loc = lastmod = ""
        for child in el:
            name = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else ""
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = (child.text or "").strip()
        match = _JOB_PATH.match(urlsplit(loc).path)
        if match:
            found[match.group(1)] = (loc, lastmod)
    rows = [(i, u, m) for i, (u, m) in found.items()]
    return sorted(rows, key=lambda r: (r[2], int(r[0])), reverse=True)


@register("jobiqo")
class JobiqoConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JobiqoConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jobiqo.json"
    config: JobiqoConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JobiqoConfig)
        return config.board_domain

    @property
    def _sitemap(self) -> str:
        return f"https://{self.config.board_domain}/sitemap.xml"

    async def fetch_jobs(self) -> FetchResult:
        rows = sitemap_jobs(await get_bytes(self.http, self._sitemap))
        if not rows:
            raise ParseError("Jobiqo: sitemap sem URLs /job/")
        own = self.config.board_domain.lower().removeprefix("www.")
        rows = [r for r in rows if _host(r[1]) == own]
        complete = len(rows) <= MAX_JOBS
        jobs: list[RawJob] = []
        for n, (ident, url, lastmod) in enumerate(rows[:MAX_JOBS]):
            if n:
                await asyncio.sleep(DELAY)
            try:
                postings = extract_job_postings(await get_text(self.http, url))
            except FetchError:
                complete = False
                continue
            if not postings:
                complete = False
                continue
            payload: dict[str, Any] = {"url": url, "lastmod": lastmod, "posting": postings[0]}
            jobs.append(RawJob(external_id=ident, payload=payload))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        posting = p.get("posting")
        if not isinstance(posting, dict):
            raise ParseError("Jobiqo: payload sem JobPosting")
        description = posting.get("description")
        if isinstance(description, str) and "&lt;" in description:
            posting = {**posting, "description": html.unescape(description)}
        title = posting.get("title")
        if isinstance(title, str) and "&" in title:
            posting = {**posting, "title": html.unescape(title)}  # '&amp;' literal no titulo
        job = job_posting_to_normalized(posting)
        return job.model_copy(
            update={"original_url": p.get("url"), "application_url": p.get("url")}
        )

    async def health_check(self) -> bool:
        try:
            sitemap_jobs(await get_bytes(self.http, self._sitemap))
        except (FetchError, ParseError):
            return False
        return True
