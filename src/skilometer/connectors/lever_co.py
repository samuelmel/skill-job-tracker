"""Conector Lever (Postings API publica, sem autenticacao).

Docs: https://github.com/lever/postings-api
Sem `limit`, a API devolve a listagem completa em uma unica resposta.
"""

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    ms_to_datetime,
    raw_text,
    salary_fields,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

API_BASES = {
    "global": "https://api.lever.co/v0/postings",
    "eu": "https://api.eu.lever.co/v0/postings",
}


class LeverConfig(BaseModel):
    company: str = Field(min_length=1, pattern=SLUG_PATTERN, description="jobs.lever.co/{company}")
    region: Literal["global", "eu"] = "global"


def _description(payload: dict[str, Any]) -> str | None:
    """HTML do anuncio: texto de abertura, listas (responsabilidades etc.) e `additional`."""
    parts: list[str] = []
    if (intro := raw_text(payload.get("description"))) is not None:
        parts.append(intro)
    lists = payload.get("lists")
    for item in lists if isinstance(lists, list) else []:
        if not isinstance(item, dict):
            continue
        content = raw_text(item.get("content"))
        if content is None:
            continue
        heading = clean(item.get("text"))
        parts.append(f"<h3>{heading}</h3><ul>{content}</ul>" if heading else f"<ul>{content}</ul>")
    if (extra := raw_text(payload.get("additional"))) is not None:
        parts.append(extra)
    return "\n".join(parts) or None


@register("lever_co")
class LeverConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = LeverConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "lever_co.json"
    config: LeverConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, LeverConfig)
        return config.company if config.region == "global" else f"{config.region}:{config.company}"

    @property
    def _url(self) -> str:
        return f"{API_BASES[self.config.region]}/{self.config.company}"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url, params={"mode": "json"})
        if not isinstance(data, list):
            raise ParseError(f"Resposta inesperada do Lever em {self._url}")
        jobs: list[RawJob] = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        categories = as_dict(p.get("categories"))
        title = clean(p.get("text")) or ""
        location = clean(categories.get("location"))
        salary = as_dict(p.get("salaryRange"))
        return NormalizedJob(
            title=title,
            description=_description(p),
            location=location,
            original_url=clean(p.get("hostedUrl")),
            application_url=clean(p.get("applyUrl")),
            original_posted_at=ms_to_datetime(p.get("createdAt")),
            employment_type=employment(categories.get("commitment")),
            workplace_type=workplace(location, title, clean(p.get("workplaceType"))),
            **salary_fields(
                salary.get("min"),
                salary.get("max"),
                salary.get("currency"),
                salary.get("interval"),
            ),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url, params={"mode": "json", "limit": 1})
        except FetchError:
            return False
        return isinstance(data, list)
