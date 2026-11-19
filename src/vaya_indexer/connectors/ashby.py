"""Conector Ashby (Posting API publica, sem autenticacao).

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    raw_text,
    salary_fields,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyConfig(BaseModel):
    org_slug: str = Field(min_length=1, pattern=SLUG_PATTERN, description="jobs.ashbyhq.com/{slug}")


def _salary(payload: dict[str, Any]) -> dict[str, Any]:
    """Salario estruturado: componente `Salary` de `compensation.summaryComponents`."""
    components = as_dict(payload.get("compensation")).get("summaryComponents")
    for item in components if isinstance(components, list) else []:
        if isinstance(item, dict) and item.get("compensationType") == "Salary":
            fields = salary_fields(
                item.get("minValue"), item.get("maxValue"), item.get("currencyCode"),
                item.get("interval"),
            )  # fmt: skip
            if fields:
                return fields
    return {}


@register("ashby")
class AshbyConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = AshbyConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "ashby.json"
    config: AshbyConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, AshbyConfig)
        return config.org_slug

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.org_slug}"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url, params={"includeCompensation": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ParseError(f"Resposta inesperada do Ashby em {self._url}")
        jobs: list[RawJob] = []
        complete = True
        for item in data["jobs"]:
            if isinstance(item, dict) and item.get("isListed") is False:
                continue  # vaga nao listada publicamente: omitida de proposito
            if not isinstance(item, dict) or not item.get("id"):
                complete = False
                continue
            jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        explicit = clean(p.get("workplaceType")) or (
            "remote" if p.get("isRemote") is True else None
        )
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("descriptionHtml")),
            location=location,
            original_url=clean(p.get("jobUrl")),
            application_url=clean(p.get("applyUrl")),
            original_posted_at=parse_iso_datetime(clean(p.get("publishedAt"))),
            employment_type=employment(p.get("employmentType")),
            workplace_type=workplace(location, title, explicit),
            **_salary(p),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, dict) and isinstance(data.get("jobs"), list)
