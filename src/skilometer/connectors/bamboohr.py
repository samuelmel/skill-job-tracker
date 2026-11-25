"""Conector BambooHR (endpoint publico da pagina de carreiras; sem autenticacao).

GET https://{subdomain}.bamboohr.com/careers/list devolve todas as vagas abertas. A
descricao so existe em /careers/{id}/detail (uma requisicao por vaga) e nao e usada, portanto
`description` fica None. Subdominios sem pagina de carreiras respondem 302 para www.bamboohr.com.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SUBDOMAIN_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError


class BambooConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{subdomain}.bamboohr.com"
    )


@register("bamboohr")
class BambooConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = BambooConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "bamboohr.json"
    config: BambooConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, BambooConfig)
        return config.subdomain

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.bamboohr.com/careers/list"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise ParseError(f"Resposta inesperada do BambooHR em {self._url}")
        jobs: list[RawJob] = []
        for item in data["result"]:
            if isinstance(item, dict) and item.get("id") is not None:
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        total = as_dict(data.get("meta")).get("totalCount")
        complete = len(jobs) == len(data["result"]) and (
            not isinstance(total, int) or total == len(jobs)
        )
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("jobOpeningName")) or ""
        loc = as_dict(p.get("location"))
        location = join_parts(loc.get("city"), loc.get("state"))
        url = f"https://{self.config.subdomain}.bamboohr.com/careers/{raw.external_id}"
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("employmentStatusLabel")),
            workplace_type=workplace(
                location, title, "remote" if p.get("isRemote") is True else None
            ),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, dict) and isinstance(data.get("result"), list)
