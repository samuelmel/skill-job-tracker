"""Conector Phenom People (widget JSON do site de carreiras, sem autenticacao).

POST https://{domain}/widgets com o corpo `refineSearch` que o proprio front-end do site usa
(`from`/`size` para paginar; resposta traz `refineSearch.totalHits` e `data.jobs`). Endpoint
nao documentado e sem host central: cada cliente tem o proprio dominio de carreiras. O
`pageId` do corpo varia por site (page11 funcionou nos testados). A API so entrega
`descriptionTeaser` (trecho curto): a descricao fica truncada. Sem salario. Se `totalHits`
exceder o recebido (ou o servidor limitar o tamanho da pagina), complete=False.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    clean,
    employment,
    join_parts,
    raw_text,
    request_json,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.pagination import paginate_offset
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime

PAGE_SIZE = 50
MAX_PAGES = 100
_HOST = r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"


class PhenomConfig(BaseModel):
    domain: str = Field(min_length=3, pattern=_HOST, description="ex.: careers.synovus.com")
    lang: str = Field(default="en_us", pattern=r"^[a-z]{2}_[a-z]{2}$")
    country: str = Field(default="us", pattern=r"^[a-z]{2}$")
    page_id: str = Field(default="page11", pattern=r"^[A-Za-z0-9_-]{1,40}$")


def _body(config: PhenomConfig, offset: int, size: int) -> dict[str, Any]:
    return {
        "lang": config.lang,
        "deviceType": "desktop",
        "country": config.country,
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "",
        "subsearch": "",
        "from": offset,
        "jobs": True,
        "counts": True,
        "all_fields": ["category", "country", "state", "city", "type"],
        "size": size,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": config.page_id,
        "siteType": "external",
        "keywords": "",
        "global": True,
        "selected_fields": {},
        "sort": {"order": "desc", "field": "postedDate"},
        "locationData": {},
    }


@register("phenom-people")
class PhenomPeopleConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PhenomConfig
    fixture_path: ClassVar[Path | None] = (
        Path(__file__).with_name("fixtures") / "phenom_people.json"
    )
    config: PhenomConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PhenomConfig)
        return config.domain

    @property
    def _url(self) -> str:
        return f"https://{self.config.domain}/widgets"

    async def _search(self, offset: int, size: int) -> dict[str, Any]:
        data = await request_json(
            self.http, "POST", self._url, json=_body(self.config, offset, size)
        )
        refine = data.get("refineSearch") if isinstance(data, dict) else None
        jobs = refine.get("data", {}).get("jobs") if isinstance(refine, dict) else None
        if not isinstance(refine, dict) or not isinstance(jobs, list):
            raise ParseError(f"Resposta inesperada do Phenom em {self._url}")
        return refine

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"total": None, "dropped": 0}

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            refine = await self._search(offset, PAGE_SIZE)
            state["total"] = refine.get("totalHits")
            raw = refine["data"]["jobs"]
            items = [
                j for j in raw if isinstance(j, dict) and (j.get("jobSeqNo") or j.get("jobId"))
            ]
            state["dropped"] += len(raw) - len(items)
            return items

        result = await paginate_offset(
            fetch_page,
            page_size=PAGE_SIZE,
            max_pages=MAX_PAGES,
            key=lambda j: str(j.get("jobSeqNo") or j.get("jobId")),
        )
        total = state["total"]
        complete = (
            result.complete
            and not state["dropped"]
            and (not isinstance(total, int) or len(result.items) >= total)
        )
        jobs = [
            RawJob(external_id=str(j.get("jobSeqNo") or j.get("jobId")), payload=j)
            for j in result.items
        ]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        places = [
            clean(m.get("location"))
            for m in p.get("multi_location_array") or []
            if isinstance(m, dict)
        ]
        location = (
            join_parts(*places, sep="; ")
            or clean(p.get("cityStateCountry"))
            or join_parts(p.get("city"), p.get("state"), p.get("country"))
        )
        url = clean(p.get("applyUrl"))
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("descriptionTeaser")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("postedDate"))),
            employment_type=employment(p.get("type")),
            workplace_type=workplace(location, title, clean(p.get("remote"))),
        )

    async def health_check(self) -> bool:
        try:
            await self._search(0, 1)
        except (FetchError, ParseError):
            return False
        return True
