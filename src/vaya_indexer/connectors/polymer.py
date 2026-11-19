"""Conector Polymer (API usada pela pagina jobs.polymer.co; sem autenticacao, nao documentada).

GET https://api.polymer.co/v1/hire/organizations/{org}/jobs, paginado por `?page=N` com
`meta.is_last`/`meta.next_page`. A listagem nao traz descricao (so a pagina da vaga).
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    parsed_salary_fields,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate_pages
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime
from vaya_indexer.processing.salary import parse_salary

API_BASE = "https://api.polymer.co/v1/hire/organizations"
MAX_PAGES = 100


def _remoteness(value: object) -> str | None:
    """`remoteness_pretty` ("Fully remote", "Hybrid", "No remote"); "No remote" nao e remoto."""
    text = (clean(value) or "").lower()
    if not text:
        return None
    if text.startswith(("no ", "not ")):
        return "onsite"
    return text


class PolymerConfig(BaseModel):
    org: str = Field(min_length=1, pattern=SLUG_PATTERN, description="jobs.polymer.co/{org}")


@register("polymer")
class PolymerConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PolymerConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "polymer.json"
    config: PolymerConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PolymerConfig)
        return config.org

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.org}/jobs"

    async def _page(self, page: int) -> dict[str, Any]:
        data = await self.http.get_json(self._url, params={"page": page})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ParseError(f"Resposta inesperada do Polymer em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"last": False, "dropped": 0}

        async def fetch_page(page: int) -> list[dict[str, Any]]:
            if state["last"]:
                return []
            data = await self._page(page)
            meta = as_dict(data.get("meta"))
            state["last"] = meta.get("is_last") is not False or not meta.get("next_page")
            items = [i for i in data["items"] if isinstance(i, dict) and i.get("id") is not None]
            state["dropped"] += len(data["items"]) - len(items)
            return items

        result = await paginate_pages(fetch_page, max_pages=MAX_PAGES, key=lambda i: str(i["id"]))
        jobs = [RawJob(external_id=str(i["id"]), payload=i) for i in result.items]
        return FetchResult(jobs=jobs, complete=result.complete and not state["dropped"])

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("display_location"))
        url = clean(p.get("job_post_url"))
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("organization_name")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(
                clean(p.get("published_at")) or clean(p.get("created_at"))
            ),
            employment_type=employment(p.get("kind_pretty")),
            workplace_type=workplace(location, title, _remoteness(p.get("remoteness_pretty"))),
            **parsed_salary_fields(parse_salary(clean(p.get("salary_pretty")))),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(1)
        except FetchError:
            return False
        return True
