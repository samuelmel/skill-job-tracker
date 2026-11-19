"""Conector JobBoard.io (sitemap + JSON-LD JobPosting; RSS so como fallback).

1. GET https://{board_domain}/sitemap.xml e um indice; os filhos `/sitemaps/jobs/...` listam as
   vagas em /jobs/{id}-{slug} (a URL do filho leva `?finish_id=&start_id=`, usada como veio).
2. GET de cada vaga e leitura do `JobPosting` em JSON-LD (uma requisicao por vaga).
O RSS (/jobs.rss) traz so as ~7 a 34 vagas mais recentes: so e usado se o sitemap falhar, e a
coleta sai com complete=False. No maximo MAX_JOBS vagas por coleta (excedente -> complete=False).
Nao ha URL externa de candidatura no HTML estatico (candidatura pelo proprio board), entao
`application_url` e a pagina da vaga. O robots.txt so bloqueia tracker/applicants/manage etc.
Plataforma distinta de jboard.io.
"""

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._boards3a import board_job, fetch_with_rss_fallback, health_probe
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register

_JOB_URL = re.compile(r"^https://[^/?#]+/jobs/(\d+)-[^/?#]+$")
_JOBS_SITEMAP = re.compile(r"/sitemaps/jobs/")


class JobBoardIoConfig(BaseModel):
    board_domain: str = Field(
        min_length=1, pattern=HOST_PATTERN, description="dominio do board, ex.: jobs.rmets.org"
    )


@register("jobboard-io")
class JobBoardIoConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JobBoardIoConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jobboard_io.json"
    config: JobBoardIoConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JobBoardIoConfig)
        return config.board_domain

    @property
    def _origin(self) -> str:
        return f"https://{self.config.board_domain}"

    async def fetch_jobs(self) -> FetchResult:
        return await fetch_with_rss_fallback(
            self.http,
            domain=self.config.board_domain,
            sitemap_url=f"{self._origin}/sitemap.xml",
            feed_url=f"{self._origin}/jobs.rss",
            job_re=_JOB_URL,
            child_re=_JOBS_SITEMAP,
        )

    def normalize(self, raw: RawJob) -> NormalizedJob:
        return board_job(raw.payload)

    async def health_check(self) -> bool:
        return await health_probe(
            self.http,
            f"{self._origin}/sitemap.xml",
            f"{self._origin}/jobs.rss",
            self.config.board_domain,
        )
