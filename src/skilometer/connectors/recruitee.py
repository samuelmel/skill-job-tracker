"""Conector Recruitee (Careers Site API publica, sem autenticacao).

Docs: https://docs.recruitee.com/reference/intro-to-careers-site-api
"""

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SUBDOMAIN_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    raw_text,
    salary_fields,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime


class RecruiteeConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{company}.recruitee.com"
    )


def _description(payload: dict[str, Any]) -> str | None:
    parts = [raw_text(payload.get("description")), raw_text(payload.get("requirements"))]
    return "\n".join(p for p in parts if p) or None


def _posted_at(value: Any) -> datetime | None:
    # "2026-11-18 16:23:32 UTC" -> ISO 8601
    text = clean(value)
    if text is None:
        return None
    return parse_iso_datetime(text.removesuffix(" UTC").replace(" ", "T"))


@register("recruitee")
class RecruiteeConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = RecruiteeConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "recruitee.json"
    config: RecruiteeConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, RecruiteeConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"https://{self.config.company}.recruitee.com/api/offers/"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
            raise ParseError(f"Resposta inesperada do Recruitee em {self._url}")
        jobs: list[RawJob] = []
        for item in data["offers"]:
            if isinstance(item, dict) and item.get("id") is not None:
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data["offers"]))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location")) or join_parts(p.get("city"), p.get("country"))
        if p.get("remote") is True:
            explicit = "remote"
        elif p.get("hybrid") is True:
            explicit = "hybrid"
        elif p.get("on_site") is True:
            explicit = "on-site"
        else:
            explicit = None
        salary = as_dict(p.get("salary"))
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("company_name")),
            description=_description(p),
            location=location,
            original_url=clean(p.get("careers_url")),
            application_url=clean(p.get("careers_apply_url")),
            original_posted_at=_posted_at(p.get("published_at")),
            employment_type=employment(p.get("employment_type_code")),
            workplace_type=workplace(location, title, explicit),
            **salary_fields(
                salary.get("min"),
                salary.get("max"),
                salary.get("currency"),
                salary.get("period"),
            ),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, dict) and isinstance(data.get("offers"), list)
