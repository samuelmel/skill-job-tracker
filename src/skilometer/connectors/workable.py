"""Conector Workable (endpoint publico v3 usado pela pagina de vagas; sem autenticacao).

POST https://apply.workable.com/api/v3/accounts/{account}/jobs, paginado por cursor: a resposta
traz `nextPage`, devolvido no corpo da proxima chamada como `token`. A listagem nao traz
descricao (so o endpoint de detalhe, que exigiria uma requisicao por vaga e nao e usado).
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    request_json,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.pagination import paginate_pages
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime

API_BASE = "https://apply.workable.com/api/v3/accounts"
JOB_URL = "https://apply.workable.com/{account}/j/{shortcode}/"
MAX_PAGES = 200

_TYPES = {
    "full": "full time",
    "part": "part time",
    "contract": "contract",
    "temporary": "temporary",
    "internship": "internship",
}


class WorkableConfig(BaseModel):
    account: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="apply.workable.com/{account}"
    )


@register("workable")
class WorkableConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = WorkableConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "workable.json"
    config: WorkableConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, WorkableConfig)
        return config.account

    @property
    def _url(self) -> str:
        return f"{API_BASE}/{self.config.account}/jobs"

    async def _page(self, token: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": "",
            "location": [],
            "department": [],
            "worktype": [],
            "remote": [],
        }
        if token:
            body["token"] = token
        data = await request_json(self.http, "POST", self._url, json=body)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ParseError(f"Resposta inesperada do Workable em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"token": None, "total": None, "dropped": 0}

        async def fetch_page(page: int) -> list[dict[str, Any]]:
            if page > 1 and not state["token"]:
                return []
            data = await self._page(state["token"])
            state["token"] = data.get("nextPage") or None
            if state["total"] is None:
                state["total"] = data.get("total")
            items = [i for i in data["results"] if isinstance(i, dict) and i.get("shortcode")]
            state["dropped"] += len(data["results"]) - len(items)
            return items

        result = await paginate_pages(
            fetch_page, max_pages=MAX_PAGES, key=lambda item: str(item["shortcode"])
        )
        total = state["total"]
        complete = (
            result.complete
            and not state["dropped"]
            and (not isinstance(total, int) or len(result.items) == total)
        )
        jobs = [RawJob(external_id=str(i["shortcode"]), payload=i) for i in result.items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        loc = as_dict(p.get("location"))
        location = join_parts(loc.get("city"), loc.get("region"), loc.get("country"))
        explicit = clean(p.get("workplace")) or ("remote" if p.get("remote") is True else None)
        kind = p.get("type")
        shortcode = clean(p.get("shortcode"))
        url = (
            JOB_URL.format(account=self.config.account, shortcode=shortcode) if shortcode else None
        )
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("published"))),
            employment_type=employment(_TYPES.get(kind, kind) if isinstance(kind, str) else None),
            workplace_type=workplace(location, title, explicit),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(None)
        except FetchError:
            return False
        return True
