"""Conector JOIN (join.com): dados embutidos na pagina publica da empresa (`__NEXT_DATA__`).

GET https://join.com/companies/{company}[?page=N]. O JSON `pageData.jobs` traz `items` e
`pagination` (pageSize 5). O parametro `page` segue a paginacao do proprio site mas nao pode ser
verificado com um quadro de mais de 5 vagas; por isso a listagem so e `complete` quando o total
declarado bate com o numero de vagas unicas obtido. A pagina nao traz descricao (so no detalhe).
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import get_text
from skilometer.connectors._common import (
    SLUG_PATTERN,
    as_dict,
    clean,
    employment,
    join_parts,
    workplace,
)
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.normalization import parse_iso_datetime

MAX_PAGES = 100
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class JoinConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="join.com/companies/{company}"
    )


def parse_page(html: str) -> dict[str, Any]:
    """`pageData.jobs` (items + pagination) do `__NEXT_DATA__`; ParseError se ausente."""
    match = _NEXT_DATA.search(html)
    if match is None:
        raise ParseError("Pagina JOIN sem __NEXT_DATA__")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise ParseError("__NEXT_DATA__ do JOIN invalido") from exc
    jobs = as_dict(as_dict(as_dict(data.get("props")).get("pageProps")).get("pageData")).get("jobs")
    if not isinstance(jobs, dict) or not isinstance(jobs.get("items"), list):
        raise ParseError("Pagina JOIN sem pageData.jobs.items")
    return jobs


@register("join_com")
class JoinConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JoinConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "join_com.json"
    config: JoinConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JoinConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"https://join.com/companies/{self.config.company}"

    async def _page(self, number: int) -> dict[str, Any]:
        params = {"page": number} if number > 1 else None
        return parse_page(await get_text(self.http, self._url, params=params))

    async def fetch_jobs(self) -> FetchResult:
        first = await self._page(1)
        pagination = as_dict(first.get("pagination"))
        page_count = pagination.get("pageCount")
        total = pagination.get("total")
        pages = [first]
        if isinstance(page_count, int) and page_count > 1:
            for number in range(2, min(page_count, MAX_PAGES) + 1):
                pages.append(await self._page(number))
        jobs: dict[str, RawJob] = {}
        complete = True
        for page in pages:
            for item in page["items"]:
                if not isinstance(item, dict) or item.get("id") is None or not item.get("idParam"):
                    complete = False
                    continue
                jobs.setdefault(str(item["id"]), RawJob(external_id=str(item["id"]), payload=item))
        if isinstance(total, int) and total != len(jobs):
            complete = False
        return FetchResult(jobs=list(jobs.values()), complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        city = as_dict(p.get("city"))
        location = join_parts(city.get("cityName"), city.get("countryName"))
        url = f"{self._url}/{p['idParam']}" if p.get("idParam") else None
        explicit = clean(p.get("remoteType")) or clean(p.get("workplaceType"))
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("createdAt"))),
            employment_type=employment(as_dict(p.get("employmentType")).get("name")),
            workplace_type=workplace(location, title, explicit.lower() if explicit else None),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(1)
        except FetchError:
            return False
        return True
