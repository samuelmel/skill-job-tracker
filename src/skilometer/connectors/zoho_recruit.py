"""Conector Zoho Recruit (pagina de carreiras publica, JSON embutido no HTML).

GET https://{company}.zohorecruit.com/jobs/Careers: a pagina vem renderizada no servidor com a
lista completa das vagas como JSON escapado em `<input id="jobs" value="[...]">` (sem
paginacao, sem JS). Parse com html.parser (o lxml nao expoe esse input). Vagas com
`Publish` falso ou `Is_Locked` verdadeiro sao omitidas de proposito. Alguns tenants nao
enviam `Job_Description` na lista (fica None). Sem salario. Pagina de ate ~2 MB. So o dominio
.com foi verificado (variantes regionais .eu/.in/.com.au nao).
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SUBDOMAIN_PATTERN,
    clean,
    employment,
    join_parts,
    raw_text,
    workplace,
)
from skilometer.connectors._web import get_text
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime


class ZohoRecruitConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{company}.zohorecruit.com"
    )


def parse_jobs(html: str) -> list[Any]:
    node = BeautifulSoup(html, "html.parser").find("input", id="jobs")
    value = node.get("value") if node is not None else None
    if not isinstance(value, str):
        raise ParseError("Zoho Recruit: input#jobs ausente na pagina de carreiras")
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise ParseError(f"Zoho Recruit: JSON de input#jobs invalido: {exc}") from exc
    if not isinstance(data, list):
        raise ParseError("Zoho Recruit: input#jobs nao e uma lista")
    return data


def _flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


@register("zoho-recruit")
class ZohoRecruitConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = ZohoRecruitConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "zoho_recruit.json"
    config: ZohoRecruitConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, ZohoRecruitConfig)
        return config.company

    @property
    def _origin(self) -> str:
        return f"https://{self.config.company}.zohorecruit.com"

    async def fetch_jobs(self) -> FetchResult:
        raw_items = parse_jobs(await get_text(self.http, f"{self._origin}/jobs/Careers"))
        jobs: list[RawJob] = []
        complete = True
        for item in raw_items:
            if not isinstance(item, dict) or not item.get("id"):
                complete = False
                continue
            if "Publish" in item and not _flag(item["Publish"]):
                continue  # nao publicada: omitida de proposito
            if _flag(item.get("Is_Locked")):
                continue
            jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("Posting_Title")) or clean(p.get("Job_Opening_Name")) or ""
        location = clean(p.get("Work_Location")) or join_parts(
            p.get("City"), p.get("State") if p.get("State") != "N/A" else None, p.get("Country")
        )
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
        job_id = clean(str(p.get("id")))
        url = f"{self._origin}/jobs/Careers/{job_id}" + (f"/{slug}" if slug else "")
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("Job_Description")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("Date_Opened"))),
            employment_type=employment(p.get("Job_Type")),
            workplace_type=workplace(
                location, title, "remote" if _flag(p.get("Remote_Job")) else None
            ),
        )

    async def health_check(self) -> bool:
        try:
            parse_jobs(await get_text(self.http, f"{self._origin}/jobs/Careers"))
        except (FetchError, ParseError):
            return False
        return True
