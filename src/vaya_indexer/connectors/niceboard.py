"""Conector Niceboard (sitemap de vagas + JSON-LD JobPosting das paginas de vaga).

1. GET https://{board_domain}/sitemap-jobs.xml lista as vagas ativas em /job/{id}-{slug}
   (sem paginacao: o sitemap traz todas).
2. GET de cada vaga: `JobPosting` em JSON-LD e, no script `var job_json = {...}`, o `apply_url`
   (candidatura externa do empregador), que vira `application_url`; sem ele (candidatura por
   formulario ou e-mail) vale a pagina da vaga.
O robots.txt traz `Crawl-delay: 1`, respeitado com uma requisicao por segundo; por isso o teto
e MAX_JOBS vagas por coleta (excedente -> complete=False). /api/ e bloqueado (exceto /api/jobs,
que nao e usado). Vaga sem JobPosting ou com detalhe falho -> complete=False.
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._boards3a import job_pairs, jobs_from_pairs, posting_to_job
from vaya_indexer.connectors._common import clean
from vaya_indexer.connectors._web import get_bytes
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.feeds import parse_xml
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError

CRAWL_DELAY = 1.0
MAX_JOBS = 200
_JOB_URL = re.compile(r"^https://[^/?#]+/job/(\d+)-[^/?#]+$")
_JOB_JSON = re.compile(r"\bvar\s+job_json\s*=\s*")


class NiceboardConfig(BaseModel):
    board_domain: str = Field(
        min_length=1, pattern=HOST_PATTERN, description="dominio do board, ex.: jobs.aicad.org"
    )


def apply_fields(html: str) -> dict[str, Any]:
    """`apply_url` externo (http/https) do `job_json` da pagina; ausente -> {}."""
    match = _JOB_JSON.search(html)
    if not match:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html, match.end())
    except ValueError:
        return {}
    url = clean(data.get("apply_url")) if isinstance(data, dict) else None
    return {"apply_url": url} if url and url.lower().startswith(("http://", "https://")) else {}


@register("niceboard")
class NiceboardConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = NiceboardConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "niceboard.json"
    config: NiceboardConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, NiceboardConfig)
        return config.board_domain

    @property
    def _sitemap(self) -> str:
        return f"https://{self.config.board_domain}/sitemap-jobs.xml"

    async def fetch_jobs(self) -> FetchResult:
        pairs = await job_pairs(self.http, self._sitemap, self.config.board_domain, _JOB_URL)
        return await jobs_from_pairs(
            self.http, pairs, extra=apply_fields, delay=CRAWL_DELAY, max_jobs=MAX_JOBS
        )

    def normalize(self, raw: RawJob) -> NormalizedJob:
        return posting_to_job(raw.payload)

    async def health_check(self) -> bool:
        try:
            parse_xml(await get_bytes(self.http, self._sitemap))
        except FetchError:
            return False
        return True
