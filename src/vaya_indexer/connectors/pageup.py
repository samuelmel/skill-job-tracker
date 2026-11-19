"""Conector PageUp (feed RSS publico do portal de carreiras, sem autenticacao).

GET https://careers.pageuppeople.com/{clientId}/cw/{lang}/rss: um unico RSS com todas as vagas
(sem paginacao). O feed nao e documentado oficialmente. Extensoes `job:*` (namespace
http://pageuppeople.com/) trazem refNo, location, workType, applyLink, closingDate e a
descricao completa em HTML. Portais em dominio proprio nao sao cobertos.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import clean, employment, raw_text, workplace
from vaya_indexer.connectors._web import element_text, get_bytes, rss_entries
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError
from vaya_indexer.processing.normalization import parse_iso_datetime

NS = "{http://pageuppeople.com/}"
ORIGIN = "https://careers.pageuppeople.com"
_EXTENSIONS = ("refNo", "description", "applyLink", "closingDate", "location", "workType")


class PageUpConfig(BaseModel):
    client_id: str = Field(pattern=r"^\d{1,9}$", description="careers.pageuppeople.com/{id}/cw/..")
    lang: str = Field(default="en", pattern=r"^[a-z]{2}(?:-[a-z]{2})?$")


@register("pageup")
class PageUpConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PageUpConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "pageup.json"
    config: PageUpConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PageUpConfig)
        return config.client_id

    @property
    def _url(self) -> str:
        return f"{ORIGIN}/{self.config.client_id}/cw/{self.config.lang}/rss"

    async def fetch_jobs(self) -> FetchResult:
        entries, dropped = rss_entries(await get_bytes(self.http, self._url))
        jobs: list[RawJob] = []
        for entry in entries:
            payload: dict[str, Any] = {
                "guid": entry.id,
                "link": entry.link,
                "title": entry.title,
                "summary": entry.summary,
                "pubDate": entry.published.isoformat() if entry.published else None,
            }
            for name in _EXTENSIONS:
                payload[name] = element_text(entry.element, f"{NS}{name}")
            jobs.append(RawJob(external_id=payload["refNo"] or entry.id, payload=payload))
        ids = [j.external_id for j in jobs]
        return FetchResult(jobs=jobs, complete=dropped == 0 and len(set(ids)) == len(ids))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        link = clean(p.get("link"))
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("description")) or raw_text(p.get("summary")),
            location=location,
            original_url=link,
            application_url=clean(p.get("applyLink")) or link,
            original_posted_at=parse_iso_datetime(clean(p.get("pubDate"))),
            employment_type=employment(p.get("workType")),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            rss_entries(await get_bytes(self.http, self._url))
        except FetchError:
            return False
        return True
