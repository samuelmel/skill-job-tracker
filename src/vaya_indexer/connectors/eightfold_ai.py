"""Conector Eightfold AI (endpoint publico `api/apply/v2/jobs` das paginas de carreiras).

GET https://{tenant}.eightfold.ai/api/apply/v2/jobs?domain={domain}&start=N&num=10. O servidor
limita a pagina a 10 itens e informa o total em `count`. A listagem nao traz descricao
(so o detalhe por vaga a tem), que fica None. Tenant com o recurso desligado responde
403/404: e falha real, nao lista vazia.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._common import SUBDOMAIN_PATTERN, clean, employment, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate_offset
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.text import html_to_text

PAGE_SIZE = 10
MAX_PAGES = 300


class EightfoldConfig(BaseModel):
    tenant: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN)
    domain: str = Field(min_length=1, pattern=HOST_PATTERN, description="ex.: netapp.com")


def _epoch(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


@register("eightfold_ai")
class EightfoldConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = EightfoldConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "eightfold_ai.json"
    config: EightfoldConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, EightfoldConfig)
        return f"{config.tenant}/{config.domain}"

    @property
    def _url(self) -> str:
        return f"https://{self.config.tenant}.eightfold.ai/api/apply/v2/jobs"

    async def _page(self, offset: int) -> dict[str, Any]:
        params = {"domain": self.config.domain, "start": offset, "num": PAGE_SIZE}
        data = await self.http.get_json(self._url, params=params)
        if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
            raise ParseError(f"Resposta inesperada do Eightfold em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"count": None}

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            data = await self._page(offset)
            state["count"] = data.get("count")
            return [i for i in data["positions"] if isinstance(i, dict)]

        result = await paginate_offset(
            fetch_page, page_size=PAGE_SIZE, max_pages=MAX_PAGES, key=lambda i: str(i.get("id"))
        )
        items = [i for i in result.items if i.get("id") is not None]
        count = state["count"]
        complete = (
            result.complete
            and len(items) == len(result.items)
            and (not isinstance(count, int) or len(items) >= count)
        )
        jobs = [RawJob(external_id=str(i["id"]), payload=i) for i in items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("name")) or clean(p.get("posting_name")) or ""
        location = clean(p.get("location"))
        url = clean(p.get("canonicalPositionUrl"))
        description = p.get("job_description")
        return NormalizedJob(
            title=title,
            description=html_to_text(description)
            if isinstance(description, str) and description.strip()
            else None,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=_epoch(p.get("t_create")),
            employment_type=employment(p.get("employment_type")),
            workplace_type=workplace(location, title, clean(p.get("work_location_option"))),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(0)
        except FetchError:
            return False
        return True
