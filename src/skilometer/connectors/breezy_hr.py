"""Conector Breezy HR (feed JSON publico da pagina de vagas; sem autenticacao).

GET https://{company}.breezy.hr/json devolve a lista completa de posicoes publicadas. O feed
nao traz descricao (ela so existe na pagina da vaga), portanto `description` fica None.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SUBDOMAIN_PATTERN,
    as_dict,
    clean,
    employment,
    parsed_salary_fields,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime
from skilometer.processing.salary import parse_salary


class BreezyConfig(BaseModel):
    company: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN, description="{company}.breezy.hr")


@register("breezy_hr")
class BreezyConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = BreezyConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "breezy_hr.json"
    config: BreezyConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, BreezyConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"https://{self.config.company}.breezy.hr/json"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, list):
            raise ParseError(f"Resposta inesperada do Breezy HR em {self._url}")
        jobs: list[RawJob] = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("name")) or ""
        loc = as_dict(p.get("location"))
        location = clean(loc.get("name"))
        url = clean(p.get("url"))
        salary = clean(p.get("salary"))
        return NormalizedJob(
            title=title,
            company_name=clean(as_dict(p.get("company")).get("name")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("published_date"))),
            employment_type=employment(as_dict(p.get("type")).get("name")),
            workplace_type=workplace(location, title, "remote" if loc.get("is_remote") else None),
            **parsed_salary_fields(parse_salary(salary)),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, list)
