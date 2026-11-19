"""Conector Manatal (API da pagina publica careers-page.com; sem autenticacao).

GET https://api.manatal.com/open/v3/career-page/{slug}/jobs/?page=N (DRF, 10 por pagina,
`count`/`next`/`results`). Endpoint observado no site publico, nao documentado; a API oficial
exige autenticacao e nao e usada. O valor de salario nao e exposto (so `is_salary_visible`).
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SLUG_PATTERN,
    clean,
    employment,
    join_parts,
    raw_text,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate_pages
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

API_BASE = "https://api.manatal.com/open/v3/career-page"
JOB_URL = "https://www.careers-page.com/{slug}/job/{hash}"
MAX_PAGES = 200


class ManatalConfig(BaseModel):
    slug: str = Field(min_length=1, pattern=SLUG_PATTERN, description="careers-page.com/{slug}")


@register("manatal")
class ManatalConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = ManatalConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "manatal.json"
    config: ManatalConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, ManatalConfig)
        return config.slug

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.slug}/jobs/"

    async def _page(self, page: int) -> dict[str, Any]:
        data = await self.http.get_json(self._url, params={"page": page})
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ParseError(f"Resposta inesperada do Manatal em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        # `next` vem com outro host (core.api.manatal.com): so o sinal de continuidade e usado.
        state: dict[str, Any] = {"last": False, "count": None, "dropped": 0}

        async def fetch_page(page: int) -> list[dict[str, Any]]:
            if state["last"]:
                return []
            data = await self._page(page)
            state["last"] = not data.get("next")
            if state["count"] is None:
                state["count"] = data.get("count")
            items = [i for i in data["results"] if isinstance(i, dict) and i.get("id") is not None]
            state["dropped"] += len(data["results"]) - len(items)
            return items

        result = await paginate_pages(fetch_page, max_pages=MAX_PAGES, key=lambda i: str(i["id"]))
        count = state["count"]
        complete = (
            result.complete
            and not state["dropped"]
            and (not isinstance(count, int) or len(result.items) == count)
        )
        jobs = [RawJob(external_id=str(i["id"]), payload=i) for i in result.items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("position_name")) or ""
        location = clean(p.get("location_display")) or join_parts(
            p.get("city"), p.get("state"), p.get("country")
        )
        job_hash = clean(p.get("hash"))
        url = JOB_URL.format(slug=self.config.slug, hash=job_hash) if job_hash else None
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("description")),
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("contract_details")),
            workplace_type=workplace(
                location, title, "remote" if p.get("is_remote") is True else None
            ),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(1)
        except FetchError:
            return False
        return True
