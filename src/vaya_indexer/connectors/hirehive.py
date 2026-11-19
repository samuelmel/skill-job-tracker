"""Conector HireHive (Jobs API v2 publica, sem autenticacao).

Docs: https://docs.hirehive.com/website-integration/jobs-api/list
Paginacao `page`/`page_size` com `meta.has_next_page` e `meta.total_items`.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import (
    SUBDOMAIN_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    parsed_salary_fields,
    raw_text,
    salary_fields,
    workplace,
)
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate_pages
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime
from vaya_indexer.processing.salary import parse_salary

PAGE_SIZE = 100
MAX_PAGES = 100


class HireHiveConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{subdomain}.hirehive.com"
    )


def _salary(payload: dict[str, Any]) -> dict[str, Any]:
    """Faixa estruturada de `compensation_tiers` (se houver) ou texto de `salary`."""
    tiers = payload.get("compensation_tiers")
    for tier in tiers if isinstance(tiers, list) else []:
        if isinstance(tier, dict) and tier.get("type") not in (None, "none"):
            fields = salary_fields(
                tier.get("min_value"),
                tier.get("max_value"),
                tier.get("currency_code"),
                tier.get("interval"),
            )
            if fields:
                return fields
    return parsed_salary_fields(parse_salary(clean(payload.get("salary"))))


@register("hirehive")
class HireHiveConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = HireHiveConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "hirehive.json"
    config: HireHiveConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, HireHiveConfig)
        return config.subdomain

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.hirehive.com/api/v2/jobs"

    async def _page(self, page: int) -> dict[str, Any]:
        data = await self.http.get_json(self._url, params={"page": page, "page_size": PAGE_SIZE})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ParseError(f"Resposta inesperada do HireHive em {self._url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"last": False, "total": None, "dropped": 0}

        async def fetch_page(page: int) -> list[dict[str, Any]]:
            if state["last"]:
                return []
            data = await self._page(page)
            meta = as_dict(data.get("meta"))
            state["last"] = meta.get("has_next_page") is not True
            if state["total"] is None:
                state["total"] = meta.get("total_items")
            items = [i for i in data["items"] if isinstance(i, dict) and i.get("id")]
            state["dropped"] += len(data["items"]) - len(items)
            return items

        result = await paginate_pages(fetch_page, max_pages=MAX_PAGES, key=lambda i: str(i["id"]))
        total = state["total"]
        complete = (
            result.complete
            and not state["dropped"]
            and (not isinstance(total, int) or len(result.items) == total)
        )
        jobs = [RawJob(external_id=str(i["id"]), payload=i) for i in result.items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        description = as_dict(p.get("description")).get("html")
        location = join_parts(p.get("location"), as_dict(p.get("country")).get("name"))
        url = clean(p.get("hosted_url"))
        return NormalizedJob(
            title=title,
            description=raw_text(description),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(
                clean(p.get("published_date")) or clean(p.get("created_date"))
            ),
            employment_type=employment(as_dict(p.get("type")).get("name")),
            workplace_type=workplace(location, title),
            **_salary(p),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(1)
        except FetchError:
            return False
        return True
