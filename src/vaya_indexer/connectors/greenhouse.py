"""Conector Greenhouse (Job Board API publica).

Docs: https://developers.greenhouse.io/job-board.html
"""

import html
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import normalize_whitespace, parse_iso_datetime

API_BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseConfig(BaseModel):
    board_token: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = normalize_whitespace(value)
    return cleaned or None


@register("greenhouse")
class GreenhouseConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = GreenhouseConfig
    config: GreenhouseConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, GreenhouseConfig)
        return config.board_token

    @property
    def _jobs_url(self) -> str:
        return f"{API_BASE}/{self.config.board_token}/jobs"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._jobs_url, params={"content": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ParseError(f"Resposta inesperada do Greenhouse em {self._jobs_url}")
        jobs: list[RawJob] = []
        for item in data["jobs"]:
            if not isinstance(item, dict) or item.get("id") is None:
                continue  # sem id nao ha como identificar; listagem passa a ser incompleta
            jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        total = (data.get("meta") or {}).get("total")
        complete = len(jobs) == len(data["jobs"]) and (total is None or total == len(jobs))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        location = p.get("location")
        content = p.get("content")
        return NormalizedJob(
            title=_clean(p.get("title")) or "",
            company_name=_clean(p.get("company_name")),
            description=html.unescape(content) if isinstance(content, str) and content else None,
            location=_clean(location.get("name")) if isinstance(location, dict) else None,
            original_url=_clean(p.get("absolute_url")),
            application_url=_clean(p.get("absolute_url")),
            original_posted_at=parse_iso_datetime(p.get("first_published")),
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(f"{API_BASE}/{self.config.board_token}")
        except FetchError:
            return False
        return isinstance(data, dict)
