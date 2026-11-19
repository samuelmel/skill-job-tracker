"""Conector Teamtailor (feed RSS publico de vagas, sem autenticacao).

GET https://{company}.teamtailor.com/jobs.rss: um unico RSS com todas as vagas (sem
paginacao). Extensoes: `remoteStatus`, `tt:department`, `tt:role` e `tt:locations`
(`tt:name`/`tt:city`/`tt:country`). Sem salario no feed. O robots.txt do Teamtailor traz
Content-Signal com ai-train=no: o conteudo serve apenas para indexacao de busca.
Docs: https://support.teamtailor.com/en/articles/6007270-rss-feed-for-your-jobs
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SUBDOMAIN_PATTERN,
    clean,
    join_parts,
    raw_text,
    workplace,
)
from vaya_indexer.connectors._web import element_text, get_bytes, rss_entries
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError
from vaya_indexer.processing.normalization import parse_iso_datetime

TT = "{https://teamtailor.com/locations}"


class TeamtailorConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{company}.teamtailor.com"
    )


def _locations(element: Any) -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    for loc in element.iterfind(f"{TT}locations/{TT}location"):
        found.append({k: element_text(loc, f"{TT}{k}") for k in ("name", "city", "country")})
    return found


@register("teamtailor")
class TeamtailorConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = TeamtailorConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "teamtailor.json"
    config: TeamtailorConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, TeamtailorConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"https://{self.config.company}.teamtailor.com/jobs.rss"

    async def fetch_jobs(self) -> FetchResult:
        entries, dropped = rss_entries(await get_bytes(self.http, self._url))
        jobs: list[RawJob] = []
        for entry in entries:
            el = entry.element
            payload: dict[str, Any] = {
                "guid": entry.id,
                "title": entry.title,
                "link": entry.link,
                "description": element_text(el, "description"),
                "pubDate": entry.published.isoformat() if entry.published else None,
                "remoteStatus": element_text(el, "remoteStatus"),
                "department": element_text(el, f"{TT}department"),
                "role": element_text(el, f"{TT}role"),
                "locations": _locations(el),
            }
            jobs.append(RawJob(external_id=entry.id, payload=payload))
        ids = [j.external_id for j in jobs]
        return FetchResult(jobs=jobs, complete=dropped == 0 and len(set(ids)) == len(ids))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        places = [
            join_parts(loc.get("name") or loc.get("city"), loc.get("country"))
            for loc in p.get("locations") or []
            if isinstance(loc, dict)
        ]
        location = join_parts(*places, sep="; ")
        status = clean(p.get("remoteStatus"))
        explicit = {"fully": "remote", "hybrid": "hybrid"}.get(status or "")
        link = clean(p.get("link"))
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("description")),
            location=location,
            original_url=link,
            application_url=link,
            original_posted_at=parse_iso_datetime(clean(p.get("pubDate"))),
            workplace_type=workplace(location, title, explicit),
        )

    async def health_check(self) -> bool:
        try:
            rss_entries(await get_bytes(self.http, self._url))
        except FetchError:
            return False
        return True
