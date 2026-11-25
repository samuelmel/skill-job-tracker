"""Conector Jobboardly (feed RSS publico `/jobs.rss` com a lista completa de vagas ativas).

GET https://{board_domain}/jobs.rss: um unico RSS 2.0 (sem paginacao) com `<description>` em HTML
completo, `<pubDate>` e a extensao `job:city|state|region|country`. O `<title>` vem como
"{cargo} - {empresa} - {local}"; empresa e local sao separados pelos dois ultimos " - " (cargos
que ja contem " - " continuam inteiros). Salario e tipo de contrato so existem no JSON-LD da
pagina da vaga e nao sao lidos aqui (um GET por vaga). `<link>` e o URL da vaga no proprio quadro
e tambem o URL de candidatura. robots.txt dos quadros testados e `Allow: /`.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import HOST_PATTERN
from skilometer.connectors._common import clean, join_parts, workplace
from skilometer.connectors._web import element_text, get_bytes, rss_entries
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime
from skilometer.processing.text import html_to_text

JOB_NS = "{https://www.jobboardly.com/rss/job}"
_SEPARATOR = " - "


class JobboardlyConfig(BaseModel):
    board_domain: str = Field(min_length=1, pattern=HOST_PATTERN)


def split_title(title: str) -> tuple[str, str | None, str | None]:
    """(cargo, empresa, local) de '{cargo} - {empresa} - {local}'; sem 3 partes, so o cargo."""
    parts = title.rsplit(_SEPARATOR, 2)
    if len(parts) == 3 and all(p.strip() for p in parts):
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    return title.strip(), None, None


@register("jobboardly")
class JobboardlyConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JobboardlyConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jobboardly.json"
    config: JobboardlyConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JobboardlyConfig)
        return config.board_domain

    @property
    def _url(self) -> str:
        return f"https://{self.config.board_domain}/jobs.rss"

    async def fetch_jobs(self) -> FetchResult:
        entries, dropped = rss_entries(await get_bytes(self.http, self._url))
        jobs: list[RawJob] = []
        for entry in entries:
            el = entry.element
            payload: dict[str, Any] = {
                "title": entry.title,
                "link": entry.link,
                "description": entry.summary,
                "published": entry.published.isoformat() if entry.published else None,
                "city": element_text(el, f"{JOB_NS}city"),
                "state": element_text(el, f"{JOB_NS}state"),
                "country": element_text(el, f"{JOB_NS}country"),
            }
            jobs.append(RawJob(external_id=entry.id, payload=payload))
        ids = [j.external_id for j in jobs]
        return FetchResult(jobs=jobs, complete=dropped == 0 and len(set(ids)) == len(ids))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title_raw = clean(p.get("title"))
        if not title_raw:
            raise ParseError("Jobboardly: vaga sem titulo")
        title, company, title_location = split_title(title_raw)
        location = join_parts(p.get("city"), p.get("state"), p.get("country")) or title_location
        link = clean(p.get("link"))
        description = p.get("description")
        return NormalizedJob(
            title=title,
            company_name=company,
            description=html_to_text(description) if isinstance(description, str) else None,
            location=location,
            original_url=link,
            application_url=link,
            original_posted_at=parse_iso_datetime(clean(p.get("published"))),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            rss_entries(await get_bytes(self.http, self._url))
        except (FetchError, ParseError):
            return False
        return True
