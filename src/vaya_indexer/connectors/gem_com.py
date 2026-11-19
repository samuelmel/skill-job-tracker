"""Conector Gem (Job Board API publica, sem autenticacao).

Docs: https://help.gem.com/databases/gem-help-center/the-job-board-api
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    raw_text,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime

API_BASE = "https://api.gem.com/job_board/v0"


class GemConfig(BaseModel):
    board_slug: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="jobs.gem.com/{board_slug}"
    )


@register("gem_com")
class GemConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = GemConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "gem_com.json"
    config: GemConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, GemConfig)
        return config.board_slug

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.board_slug}/job_posts/"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._url)
        if not isinstance(data, list):
            raise ParseError(f"Resposta inesperada do Gem em {self._url}")
        jobs: list[RawJob] = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        return FetchResult(jobs=jobs, complete=len(jobs) == len(data))

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(as_dict(p.get("location")).get("name"))
        url = clean(p.get("absolute_url"))
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("content")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(
                clean(p.get("first_published_at")) or clean(p.get("created_at"))
            ),
            employment_type=employment(p.get("employment_type")),
            workplace_type=workplace(location, title, clean(p.get("location_type"))),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._url)
        except FetchError:
            return False
        return isinstance(data, list)
