"""Conector Personio (feed XML publico de vagas, sem autenticacao).

GET https://{account}.jobs.personio.{com|de}/xml?language={lang}: um unico documento
`<workzag-jobs>` com todas as `<position>`; sem paginacao. O tenant precisa ter o feed XML
ativado. Nao ha salario nem URL de candidatura no feed: a pagina da vaga e /job/{id}.
Docs: https://support.personio.de/hc/en-us/articles/207576365
"""

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SUBDOMAIN_PATTERN,
    clean,
    employment,
    join_parts,
    raw_text,
    workplace,
)
from vaya_indexer.connectors._web import element_text, get_bytes
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.feeds import parse_xml
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime

_FIELDS = (
    "id", "subcompany", "office", "department", "recruitingCategory", "name",
    "employmentType", "seniority", "schedule", "createdAt",
)  # fmt: skip


class PersonioConfig(BaseModel):
    account: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN, description="{account}.jobs.*")
    tld: Literal["com", "de"] = "com"
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")


def _position(el: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {f: element_text(el, f) for f in _FIELDS}
    payload["additionalOffices"] = [
        t for o in el.iterfind("additionalOffices/office") if (t := (o.text or "").strip())
    ]
    payload["jobDescriptions"] = [
        {"name": element_text(item, "name"), "value": (item.findtext("value") or "").strip()}
        for item in el.iterfind("jobDescriptions/jobDescription")
    ]
    return payload


def _employment(p: dict[str, Any]) -> str | None:
    kind = clean(p.get("employmentType"))
    if kind == "intern":
        return "INTERNSHIP"
    return employment(p.get("schedule"))


@register("personio")
class PersonioConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PersonioConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "personio.json"
    config: PersonioConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PersonioConfig)
        return f"{config.account}.{config.tld}"

    @property
    def _origin(self) -> str:
        return f"https://{self.config.account}.jobs.personio.{self.config.tld}"

    async def _root(self) -> Any:
        url = f"{self._origin}/xml"
        root = parse_xml(await get_bytes(self.http, url, params={"language": self.config.language}))
        if root.tag != "workzag-jobs":
            raise ParseError(f"Raiz inesperada do feed Personio em {url}: {root.tag}")
        return root

    async def fetch_jobs(self) -> FetchResult:
        root = await self._root()
        jobs: list[RawJob] = []
        complete = True
        for el in root.iterfind("position"):
            payload = _position(el)
            if not payload.get("id") or not payload.get("name"):
                complete = False
                continue
            jobs.append(RawJob(external_id=str(payload["id"]), payload=payload))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("name")) or ""
        parts: list[str] = []
        for item in p.get("jobDescriptions") or []:
            value = raw_text(item.get("value")) if isinstance(item, dict) else None
            if value is None:
                continue
            heading = clean(item.get("name"))
            parts.append(f"{heading}\n{value}" if heading else value)
        location = join_parts(p.get("office"), *(p.get("additionalOffices") or []), sep="; ")
        job_id = clean(p.get("id"))
        url = f"{self._origin}/job/{job_id}" if job_id else None
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("subcompany")),
            description="\n\n".join(parts) or None,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("createdAt"))),
            employment_type=_employment(p),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            await self._root()
        except FetchError:
            return False
        return True
