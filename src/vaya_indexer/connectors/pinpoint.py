"""Conector Pinpoint (feed publico /postings.json da pagina de vagas; sem autenticacao).

Docs: https://developers.pinpointhq.com/
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SUBDOMAIN_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    raw_text,
    salary_fields,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

_SECTIONS = (
    ("description", None),
    ("key_responsibilities", "key_responsibilities_header"),
    ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
    ("benefits", "benefits_header"),
)


class PinpointConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{subdomain}.pinpointhq.com"
    )


def _description(payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for field, header_field in _SECTIONS:
        body = raw_text(payload.get(field))
        if body is None:
            continue
        header = clean(payload.get(header_field)) if header_field else None
        parts.append(f"<h3>{header}</h3>\n{body}" if header else body)
    return "\n".join(parts) or None


@register("pinpoint")
class PinpointConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PinpointConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "pinpoint.json"
    config: PinpointConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PinpointConfig)
        return config.subdomain

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.pinpointhq.com/postings.json"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ParseError(f"Resposta inesperada do Pinpoint em {self._url}")
        jobs: list[RawJob] = []
        for item in data["data"]:
            if isinstance(item, dict) and item.get("id") is not None:
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data["data"]))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        loc = as_dict(p.get("location"))
        location = join_parts(loc.get("city"), loc.get("province"), loc.get("name"))
        url = clean(p.get("url"))
        salary: dict[str, Any] = {}
        if p.get("compensation_visible") is True:
            salary = salary_fields(
                p.get("compensation_minimum"),
                p.get("compensation_maximum"),
                p.get("compensation_currency"),
                p.get("compensation_frequency"),
            )
        return NormalizedJob(
            title=title,
            description=_description(p),
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("employment_type_text")),
            workplace_type=workplace(location, title, clean(p.get("workplace_type"))),
            **salary,
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, dict) and isinstance(data.get("data"), list)
