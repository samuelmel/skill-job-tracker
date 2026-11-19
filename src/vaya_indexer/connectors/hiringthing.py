"""Conector HiringThing (feed RSS publico, sem autenticacao).

GET https://{subdomain}.hiringthing.com/api/rss.xml: um unico RSS com todas as vagas (sem
paginacao). `<description>` e um trecho truncado; o HTML completo vem em `media:description`.
O feed nao traz data nem salario. robots.txt do host so bloqueia /admin.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import SUBDOMAIN_PATTERN, clean, raw_text, workplace
from vaya_indexer.connectors._web import element_text, get_bytes, rss_entries
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError

MEDIA = "{http://search.yahoo.com/mrss/}"


class HiringThingConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{subdomain}.hiringthing.com"
    )


@register("hiringthing")
class HiringThingConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = HiringThingConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "hiringthing.json"
    config: HiringThingConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, HiringThingConfig)
        return config.subdomain

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.hiringthing.com/api/rss.xml"

    async def fetch_jobs(self) -> FetchResult:
        entries, dropped = rss_entries(await get_bytes(self.http, self._url))
        jobs: list[RawJob] = []
        for entry in entries:
            payload: dict[str, Any] = {
                "title": entry.title,
                "link": entry.link,
                "summary": entry.summary,
                "location": element_text(entry.element, "location"),
                "description": element_text(entry.element, f"{MEDIA}description"),
            }
            jobs.append(RawJob(external_id=entry.id, payload=payload))
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
            application_url=link,
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            rss_entries(await get_bytes(self.http, self._url))
        except FetchError:
            return False
        return True
