"""Conector iCIMS (portal de carreiras `{portal}.icims.com`, HTML server-side).

GET https://{host}/jobs/search?ss=1&in_iframe=1&pr=N (pr 0-based). Cada vaga e um
`li.iCIMS_JobCardItem` com link `/jobs/{id}/{slug}/job` e campos rotulados (`dt`/`dd`). O total
de paginas vem de "Page X of Y". O robots.txt padrao nao proibe `/jobs/search`. A descricao so
existe no detalhe (fica None). Pagina sem cartoes nem paginacao nao e uma listagem valida:
ParseError, nunca lista vazia "completa".
"""

import re
from pathlib import Path
from typing import ClassVar

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN, get_text
from vaya_indexer.connectors._common import clean, employment, join_parts, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

MAX_PAGES = 200
_JOB_ID = re.compile(r"/jobs/(\d+)/")
_PAGE_OF = re.compile(r"Page\s+\d+\s+of\s+(\d+)")


class IcimsConfig(BaseModel):
    host: str = Field(min_length=1, pattern=HOST_PATTERN, description="ex.: careers-acme.icims.com")


def parse_page(html: str) -> tuple[list[dict[str, str | None]], int | None]:
    """(vagas da pagina, total de paginas declarado)."""
    soup = BeautifulSoup(html, "lxml")
    jobs: list[dict[str, str | None]] = []
    for card in soup.select("li.iCIMS_JobCardItem"):
        link = card.select_one("div.title a[href]")
        match = _JOB_ID.search(str(link.get("href"))) if link else None
        if link is None or match is None:
            continue
        fields: dict[str, str] = {}
        for tag in card.select("div.iCIMS_JobHeaderTag"):
            label = tag.select_one("dt")
            value = tag.select_one("dd")
            if label and value:
                fields[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
        title = link.select_one("h3")
        jobs.append(
            {
                "id": match.group(1),
                "title": (title or link).get_text(" ", strip=True),
                "url": str(link.get("href")),
                "location": join_parts(*(v for k, v in fields.items() if "Location" in k)),
                "employment_type": next(
                    (v for k, v in fields.items() if k.endswith("Type") or " Type" in k), None
                ),
            }
        )
    paging = _PAGE_OF.search(soup.get_text(" ", strip=True))
    return jobs, int(paging.group(1)) if paging else None


@register("icims")
class IcimsConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = IcimsConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "icims.json"
    config: IcimsConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, IcimsConfig)
        return config.host

    @property
    def _url(self) -> str:
        return f"https://{self.config.host}/jobs/search"

    async def _page(self, number: int) -> tuple[list[dict[str, str | None]], int | None]:
        params = {"ss": 1, "in_iframe": 1, "pr": number}
        jobs, pages = parse_page(await get_text(self.http, self._url, params=params))
        if not jobs and pages is None:
            raise ParseError(f"Pagina {self._url} sem vagas nem paginacao iCIMS")
        return jobs, pages

    async def fetch_jobs(self) -> FetchResult:
        first, pages = await self._page(0)
        collected = list(first)
        complete = True
        total_pages = pages or 1
        if total_pages > MAX_PAGES:
            complete = False
            total_pages = MAX_PAGES
        for number in range(1, total_pages):
            rows, _ = await self._page(number)
            collected.extend(rows)
        seen: dict[str, RawJob] = {}
        for row in collected:
            seen.setdefault(str(row["id"]), RawJob(external_id=str(row["id"]), payload=dict(row)))
        if len(seen) != len(collected):
            complete = False  # paginas se sobrepoem: a ordenacao mudou durante a coleta
        return FetchResult(jobs=list(seen.values()), complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        url = clean(p.get("url"))
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("employment_type")),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(0)
        except FetchError:
            return False
        return True
