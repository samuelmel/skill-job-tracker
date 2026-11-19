"""Conector Workday (endpoint CXS da pagina de carreiras; sem autenticacao, nao documentado).

POST https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs com corpo
{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}. `total` so vem preenchido na
primeira pagina (offset 0). A listagem nao traz descricao nem data exata (`postedOn` e texto
relativo: "Posted Today"), que ficam None.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SLUG_PATTERN,
    SUBDOMAIN_PATTERN,
    clean,
    request_json,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate_offset
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

PAGE_SIZE = 20
MAX_PAGES = 400


class WorkdayConfig(BaseModel):
    tenant: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN)
    host: str = Field(pattern=r"^wd\d+$", description="datacenter, ex.: wd1, wd5, wd12")
    site: str = Field(min_length=1, pattern=SLUG_PATTERN, description="nome do site de carreiras")


@register("workday")
class WorkdayConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = WorkdayConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "workday.json"
    config: WorkdayConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, WorkdayConfig)
        return f"{config.tenant}.{config.host}/{config.site}"

    @property
    def _origin(self) -> str:
        return f"https://{self.config.tenant}.{self.config.host}.myworkdayjobs.com"

    @property
    def _url(self) -> str:
        return f"{self._origin}/wday/cxs/{self.config.tenant}/{self.config.site}/jobs"

    async def _page(self, offset: int) -> dict[str, Any]:
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        data = await request_json(self.http, "POST", self._url, json=body)
        if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
            raise ParseError(f"Resposta inesperada do Workday em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"total": None}

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            data = await self._page(offset)
            if offset == 0:
                state["total"] = data.get("total")
            return [i for i in data["jobPostings"] if isinstance(i, dict)]

        result = await paginate_offset(
            fetch_page,
            page_size=PAGE_SIZE,
            max_pages=MAX_PAGES,
            key=lambda i: str(i.get("externalPath") or sorted(i.items())),
        )
        items = [i for i in result.items if i.get("externalPath")]
        total = state["total"]
        complete = (
            result.complete
            and len(items) == len(result.items)  # vaga sem externalPath nao tem identidade
            and (not isinstance(total, int) or len(items) >= total)
        )
        jobs = [RawJob(external_id=str(i["externalPath"]), payload=i) for i in items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("locationsText"))
        path = clean(p.get("externalPath"))
        url = f"{self._origin}/{self.config.site}{path}" if path else None
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(0)
        except FetchError:
            return False
        return True
