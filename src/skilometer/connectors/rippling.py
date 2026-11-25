"""Conector Rippling (API publica do quadro ats.rippling.com; sem autenticacao).

GET https://api.rippling.com/platform/api/ats/v1/board/{board}/jobs devolve a lista completa.
A listagem so traz uuid, nome, departamento, URL e local: sem descricao, data ou salario.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import SLUG_PATTERN, as_dict, clean, workplace
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"


class RipplingConfig(BaseModel):
    board: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="ats.rippling.com/{board}/jobs"
    )


@register("rippling")
class RipplingConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = RipplingConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "rippling.json"
    config: RipplingConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, RipplingConfig)
        return config.board

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.board}/jobs"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, list):
            raise ParseError(f"Resposta inesperada do Rippling em {self._url}")
        jobs: list[RawJob] = []
        for item in data:
            if isinstance(item, dict) and item.get("uuid"):
                jobs.append(RawJob(external_id=str(item["uuid"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("name")) or ""
        location = clean(as_dict(p.get("workLocation")).get("label"))
        url = clean(p.get("url"))
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, list)
