"""Conector JBoard.io (sitemap + JSON-LD JobPosting das paginas de vaga).

O robots.txt dos quadros JBoard.io bloqueia `/rss/` e pede `Crawl-delay: 1`, entao o feed RSS NAO
e usado. `/sitemap.xml` e um sitemapindex com shards `sitemap-jobs-N.xml` (urlset de
`/jobs/{id}-{slug}`, ids crescentes); sitemaps planos com as vagas tambem sao aceitos. Cada
vaga exige um GET da pagina (JSON-LD: titulo, descricao, empresa, local, datas). Para respeitar o
Crawl-delay as paginas sao lidas em sequencia com `DELAY` entre elas e so as `MAX_JOBS` (1000) mais
novas (maior id) entram; se houver mais, `complete=False`. O JSON-LD nao traz `url`: o URL da
vaga e o da pagina e o de candidatura e `/jobs/{id}/apply` (o formulario do quadro).
"""

import asyncio
import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import HOST_PATTERN
from skilometer.connectors._web import get_bytes, get_text, sitemap_locs
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, HttpStatusError, ParseError

MAX_JOBS = 1000
DELAY = 1.0  # segundos entre paginas de vaga (Crawl-delay: 1 do robots.txt)
_JOB_PATH = re.compile(r"^/jobs/(\d+)(?:-[^/]*)?/?$")
_SHARD_PATH = re.compile(r"sitemap[^/]*jobs[^/]*\.xml$", re.IGNORECASE)


class JBoardIoConfig(BaseModel):
    board_domain: str = Field(min_length=1, pattern=HOST_PATTERN)


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def job_id(url: str) -> str | None:
    match = _JOB_PATH.match(urlsplit(url).path)
    return match.group(1) if match else None


@register("jboard-io")
class JBoardIoConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JBoardIoConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jboard_io.json"
    config: JBoardIoConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JBoardIoConfig)
        return config.board_domain

    @property
    def _origin(self) -> str:
        return f"https://{self.config.board_domain}"

    def _same_host(self, url: str) -> bool:
        return _host(url) == self.config.board_domain.lower().removeprefix("www.")

    async def _job_urls(self) -> tuple[dict[str, str], bool]:
        """{id: url} das vagas do sitemap e se todos os shards foram lidos."""
        locs = sitemap_locs(await get_bytes(self.http, f"{self._origin}/sitemap.xml"))
        found = {i: u for u in locs if self._same_host(u) and (i := job_id(u))}
        shards = [u for u in locs if self._same_host(u) and _SHARD_PATH.search(urlsplit(u).path)]
        complete = True
        for shard in sorted(shards, reverse=True):  # shards novos primeiro
            if len(found) >= MAX_JOBS:
                complete = False
                break
            try:
                shard_locs = sitemap_locs(await get_bytes(self.http, shard))
            except FetchError:
                complete = False
                continue
            for loc in shard_locs:
                if self._same_host(loc) and (i := job_id(loc)):
                    found[i] = loc
        return found, complete

    async def fetch_jobs(self) -> FetchResult:
        found, complete = await self._job_urls()
        if not found:
            raise ParseError("JBoard.io: sitemap sem URLs de vaga")
        newest = sorted(found, key=int, reverse=True)
        if len(newest) > MAX_JOBS:
            complete = False
        jobs: list[RawJob] = []
        for n, ident in enumerate(newest[:MAX_JOBS]):
            if n:
                await asyncio.sleep(DELAY)
            url = found[ident]
            try:
                postings = extract_job_postings(await get_text(self.http, url))
            except HttpStatusError as exc:
                # 404/410: a vaga saiu do ar depois de gerado o sitemap; nao e truncamento.
                if exc.status_code not in (404, 410):
                    complete = False
                continue
            except FetchError:
                complete = False
                continue
            if not postings:
                complete = False  # vaga removida/sem JSON-LD
                continue
            payload: dict[str, Any] = {
                "url": url,
                "apply_url": f"{self._origin}/jobs/{ident}/apply",
                "posting": postings[0],
            }
            jobs.append(RawJob(external_id=ident, payload=payload))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        posting = p.get("posting")
        if not isinstance(posting, dict):
            raise ParseError("JBoard.io: payload sem JobPosting")
        job = job_posting_to_normalized(posting)
        return job.model_copy(
            update={"original_url": p.get("url"), "application_url": p.get("apply_url")}
        )

    async def health_check(self) -> bool:
        try:
            sitemap_locs(await get_bytes(self.http, f"{self._origin}/sitemap.xml"))
        except (FetchError, ParseError):
            return False
        return True
