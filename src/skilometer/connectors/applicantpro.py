"""Conector ApplicantPro (endpoint JSON usado pela propria pagina de carreiras; sem auth).

GET https://{subdomain}.applicantpro.com/core/jobs/{domain_id}?getParams={"isInternal":0}.
O endpoint devolve no maximo 300 vagas e informa o total real em `data.jobCount`; sem
parametro de paginacao conhecido. Quando `jobCount` excede o devolvido, `complete=False`.
A descricao so existe na pagina de detalhe (HTML) e fica None; a moeda do salario nao e
informada (so `minSalary`/`maxSalary` numericos), entao o salario fica None.
"""

import json
from pathlib import Path
from typing import Any, ClassVar

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

RESULT_CAP = 300


class ApplicantProConfig(BaseModel):
    subdomain: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN)
    domain_id: int = Field(gt=0, description="numerico; 'domainId' em {sub}.applicantpro.com/jobs/")


@register("applicantpro")
class ApplicantProConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = ApplicantProConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "applicantpro.json"
    config: ApplicantProConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, ApplicantProConfig)
        return f"{config.subdomain}/{config.domain_id}"

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.applicantpro.com/core/jobs/{self.config.domain_id}"

    async def _data(self) -> dict[str, Any]:
        params = {"getParams": json.dumps({"isInternal": 0}, separators=(",", ":"))}
        body = await self.http.get_json(self._url, params=params)
        data = as_dict(as_dict(body).get("data"))
        if not isinstance(data.get("jobs"), list):
            raise ParseError(f"Resposta inesperada do ApplicantPro em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        data = await self._data()
        jobs: list[RawJob] = []
        complete = True
        for item in data["jobs"]:
            if not isinstance(item, dict) or item.get("id") is None:
                complete = False
                continue
            jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        total = data.get("jobCount")
        # Limite de 300 do endpoint: total declarado maior que o recebido = listagem truncada.
        if isinstance(total, int) and total > len(data["jobs"]):
            complete = False
        elif len(data["jobs"]) >= RESULT_CAP and not isinstance(total, int):
            complete = False
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("jobLocation")) or join_parts(
            p.get("city"), p.get("abbreviation") or p.get("stateName"), p.get("iso3")
        )
        url = clean(p.get("jobUrl"))
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("employmentType")),
            workplace_type=workplace(location, title, clean(p.get("workplaceType"))),
        )

    async def health_check(self) -> bool:
        try:
            await self._data()
        except FetchError:
            return False
        return True
