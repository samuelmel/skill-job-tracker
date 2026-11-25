"""Conector iSolvedHire (sitemap publico + JSON-LD JobPosting das paginas de vaga).

1. GET https://{subdomain}.isolvedhire.com/job_site_map.xml lista as URLs /jobs/{id}.html.
2. GET de cada URL e leitura do `JobPosting` em JSON-LD (uma requisicao por vaga).
A listagem /jobs/ e um web component Vue (sem dados no HTML), por isso o sitemap e a fonte.
O robots.txt so bloqueia /admin/, /stats/ e /internaljobs*. Limites: no maximo MAX_JOBS vagas
por coleta (excedente -> complete=False); vaga cujo detalhe falhou -> complete=False.
Salario fica None: `unitText` vem vazio e os valores trazem virgulas ("170,000").
"""

import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import SUBDOMAIN_PATTERN, clean
from skilometer.connectors._web import gather_details, get_bytes, get_text, sitemap_locs
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError
from skilometer.processing.normalization import parse_iso_datetime

MAX_JOBS = 200
_JOB_URL = re.compile(r"^https://[A-Za-z0-9.-]+/jobs/(\d+)\.html$")


class IsolvedHireConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{subdomain}.isolvedhire.com"
    )


@register("isolvedhire")
class IsolvedHireConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = IsolvedHireConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "isolvedhire.json"
    config: IsolvedHireConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, IsolvedHireConfig)
        return config.subdomain

    @property
    def _origin(self) -> str:
        return f"https://{self.config.subdomain}.isolvedhire.com"

    async def _job_urls(self) -> list[tuple[str, str]]:
        locs = sitemap_locs(await get_bytes(self.http, f"{self._origin}/job_site_map.xml"))
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
        posting.pop("baseSalary", None)  # valores ambiguos (unitText vazio, separador de milhar)
        job = job_posting_to_normalized(posting)
        posted = clean(posting.get("datePosted"))
        url = clean(raw.payload.get("url")) or job.original_url
        return job.model_copy(
            update={
                "original_url": url,
                "application_url": url,
                "original_posted_at": parse_iso_datetime(posted[:10]) if posted else None,
            }
        )

    async def health_check(self) -> bool:
        try:
            await self._job_urls()
        except FetchError:
            return False
        return True
