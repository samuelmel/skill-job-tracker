"""Conector SmartJobBoard (sitemap + JSON-LD JobPosting; RSS so como fallback).

1. GET https://{board_domain}/sitemap.xml lista as vagas em /job/{id}/{slug}/ (mais paginas de
   cidades, empresas, categorias etc., que sao ignoradas).
2. GET de cada vaga e leitura do `JobPosting` em JSON-LD (uma requisicao por vaga).
O RSS (/rss) e truncado pela plataforma (29 a 50 itens): so e usado se o sitemap falhar, e a
coleta sai com complete=False. No maximo MAX_JOBS vagas por coleta (excedente -> complete=False).
O robots.txt dos boards bloqueia /files/, /download/ e /application-redirect/: o link de
candidatura da pagina passa por esse redirect, entao `application_url` e a propria pagina da vaga.
Salario so entra se o JSON-LD trouxer valores numericos (o `baseSalary` costuma vir vazio).
"""

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._boards3a import board_job, fetch_with_rss_fallback, health_probe
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register

_JOB_URL = re.compile(r"^https://[^/?#]+/job/(\d+)/[^/?#]*/?$")


class SmartJobBoardConfig(BaseModel):
    board_domain: str = Field(
        min_length=1, pattern=HOST_PATTERN, description="dominio do board, ex.: jobs.acem.org.au"
    )


@register("smartjobboard")
class SmartJobBoardConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = SmartJobBoardConfig
    fixture_path: ClassVar[Path | None] = (
        Path(__file__).with_name("fixtures") / "smartjobboard.json"
    )
    config: SmartJobBoardConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, SmartJobBoardConfig)
        return config.board_domain

    @property
    def _origin(self) -> str:
        return f"https://{self.config.board_domain}"

    async def fetch_jobs(self) -> FetchResult:
        return await fetch_with_rss_fallback(
            self.http,
            domain=self.config.board_domain,
            sitemap_url=f"{self._origin}/sitemap.xml",
            feed_url=f"{self._origin}/rss",
            job_re=_JOB_URL,
        )

    def normalize(self, raw: RawJob) -> NormalizedJob:
        return board_job(raw.payload)

    async def health_check(self) -> bool:
        return await health_probe(
            self.http,
            f"{self._origin}/sitemap.xml",
            f"{self._origin}/rss",
            self.config.board_domain,
        )
