"""Conector Trakstar Hire (sitemap publico + JSON-LD JobPosting das paginas de vaga).

1. GET https://{company}.hire.trakstar.com/sitemap.xml lista as URLs /jobs/{id}.
2. GET de cada URL e leitura do `JobPosting` em JSON-LD (uma requisicao por vaga). O
   JSON-LD traz quebras de linha cruas (parse tolerante) e a descricao com HTML escapado
   (entidades), entao e desescapada antes de virar texto.
O robots.txt libera "/" e bloqueia /prepare* e /published_jobs_list_v2*. A API oficial exige
chave e nao e usada. No maximo MAX_JOBS vagas por coleta (excedente -> complete=False); vaga
cujo detalhe falhou -> complete=False. O JSON-LD nao informa a URL: ela vem do sitemap.
"""

import html
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import SUBDOMAIN_PATTERN, clean
from vaya_indexer.connectors._web import gather_details, get_bytes, get_text, sitemap_locs
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError

MAX_JOBS = 500
_JOB_URL = re.compile(r"^https://[A-Za-z0-9.-]+/jobs/([A-Za-z0-9]+)/?$")


class TrakstarConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{company}.hire.trakstar.com"
    )


@register("trakstar")
class TrakstarConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = TrakstarConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "trakstar.json"
    config: TrakstarConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, TrakstarConfig)
        return config.company

    @property
    def _origin(self) -> str:
        return f"https://{self.config.company}.hire.trakstar.com"

    async def _job_urls(self) -> list[tuple[str, str]]:
        locs = sitemap_locs(await get_bytes(self.http, f"{self._origin}/sitemap.xml"))
        pairs = []
        for loc in locs:
            match = _JOB_URL.match(loc)
            if match and loc.startswith(self._origin + "/"):
                pairs.append((match.group(1), loc))
        return pairs

    async def _detail(self, pair: tuple[str, str]) -> dict[str, Any] | None:
        postings = extract_job_postings(await get_text(self.http, pair[1]))
        return postings[0] if postings else None

    async def fetch_jobs(self) -> FetchResult:
        pairs = await self._job_urls()
        complete = len(pairs) <= MAX_JOBS
        pairs = pairs[:MAX_JOBS]
        details = await gather_details(pairs, self._detail)
        jobs: list[RawJob] = []
        for (job_id, url), posting in zip(pairs, details, strict=True):
            if posting is None:
                complete = False
                continue
            jobs.append(RawJob(external_id=job_id, payload={"url": url, "posting": posting}))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        posting = dict(raw.payload.get("posting") or {})
        for key in ("title", "description"):
            if isinstance(posting.get(key), str):
                posting[key] = html.unescape(posting[key])
        job = job_posting_to_normalized(posting)
        url = clean(raw.payload.get("url"))
        return job.model_copy(update={"original_url": url, "application_url": url})

    async def health_check(self) -> bool:
        try:
            await self._job_urls()
        except FetchError:
            return False
        return True
