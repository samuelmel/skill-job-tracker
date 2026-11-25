"""Conector TriNet Hire (HTML do portal publico, sem API).

1. GET https://app.trinethire.com/companies/{id}-{slug}/jobs: tabela `table.job-list` com
   link (`/companies/{id}-{slug}/jobs/{jobId}-{slug}`), tipo (`td.type`) e local (`td.location`).
2. GET de cada vaga: descricao em `div.job-descr.content` (nao ha JSON-LD).
Sem paginacao observada. USO RESTRITO: o robots.txt do host declara Content-Signal
`search=yes, ai-train=no` (e bloqueia crawlers de IA nomeados); os dados servem so para
indexacao de busca de vagas, nunca para treino de modelos. Seletores sao frageis: layout
alterado -> ParseError ou menos vagas. Detalhe que falhar mantem a vaga sem descricao
(complete=False). No maximo MAX_JOBS vagas por coleta.
"""

import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from skilometer.connectors._common import clean, employment, raw_text, workplace
from skilometer.connectors._web import gather_details, get_text, make_soup
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

ORIGIN = "https://app.trinethire.com"
MAX_JOBS = 100


class TriNetConfig(BaseModel):
    company: str = Field(
        pattern=r"^\d+-[A-Za-z0-9][A-Za-z0-9-]*$", description="id-slug de /companies/{id}-{slug}"
    )


def parse_listing(html: str, company: str) -> list[dict[str, Any]]:
    soup = make_soup(html)
    prefix = f"/companies/{company}/jobs/"
    items: list[dict[str, Any]] = []
    for row in soup.select("table.job-list tr.job"):
        link = row.select_one("a[href]")
        href = link.get("href") if link is not None else None
        title = clean(link.get_text(" ", strip=True)) if link is not None else None
        if not isinstance(href, str) or not title:
            continue
        path = urlsplit(href).path
        match = re.match(r"^(\d+)-[^/]+$", path[len(prefix) :]) if path.startswith(prefix) else None
        if not match:
            continue
        kind = row.select_one("td.type")
        place = row.select_one("td.location")
        items.append(
            {
                "id": match.group(1),
                "title": title,
                "url": f"{ORIGIN}{path}",
                "type": clean(kind.get_text(" ", strip=True)) if kind is not None else None,
                "location": clean(place.get_text(" ", strip=True)) if place is not None else None,
            }
        )
    return items


def parse_description(html: str) -> str | None:
    node = make_soup(html).select_one("div.job-descr.content")
    return raw_text(node.decode_contents()) if node is not None else None


@register("trinet")
class TriNetConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = TriNetConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "trinet.json"
    config: TriNetConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, TriNetConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"{ORIGIN}/companies/{self.config.company}/jobs"

    async def _detail(self, item: dict[str, Any]) -> str | None:
        return parse_description(await get_text(self.http, item["url"]))

    async def fetch_jobs(self) -> FetchResult:
        items = parse_listing(await get_text(self.http, self._url), self.config.company)
        unique = list({i["id"]: i for i in items}.values())
        complete = len(unique) == len(items) and len(unique) <= MAX_JOBS
        unique = unique[:MAX_JOBS]
        details = await gather_details(unique, self._detail)
        jobs: list[RawJob] = []
        for item, description in zip(unique, details, strict=True):
            if description is None:
                complete = False
            jobs.append(
                RawJob(external_id=item["id"], payload={**item, "description": description})
            )
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        url = clean(p.get("url"))
        return NormalizedJob(
            title=title,
            description=raw_text(p.get("description")),
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("type")),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            parse_listing(await get_text(self.http, self._url), self.config.company)
        except (FetchError, ParseError):
            return False
        return True
