"""Conector Hireology (HTML do portal de carreiras, sem API publica).

GET https://careers.hireology.com/{slug}?page=N: Next.js renderizado no servidor, 10 cartoes
`li[data-qa-id=job-card]` por pagina e o total declarado em "N jobs found". Nao existe API
JSON publica (api.hireology.com/v2/career_sites/{slug}/jobs retorna 404) e o robots.txt
libera tudo. A listagem nao traz descricao (fica None; nao ha busca de detalhe nesta versao).
Os seletores `data-qa-id` sao a parte fragil: se mudarem, a coleta falha por ParseError ou
volta com menos vagas que o total declarado (complete=False), nunca com dados inventados.
"""

import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from skilometer.connectors._common import (
    SLUG_PATTERN,
    clean,
    employment,
    parsed_salary_fields,
    workplace,
)
from skilometer.connectors._web import count_hint, get_text, make_soup
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.pagination import paginate_pages
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError
from skilometer.processing.salary import parse_salary

ORIGIN = "https://careers.hireology.com"
PAGE_SIZE = 10
MAX_PAGES = 100
_ID_RE = re.compile(r"^/[^/]+/(\d+)/description/?$")


class HireologyConfig(BaseModel):
    slug: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="careers.hireology.com/{slug}"
    )


def _text(card: Any, qa_id: str) -> str | None:
    node = card.select_one(f'[data-qa-id="{qa_id}"]')
    return clean(node.get_text(" ", strip=True)) if node is not None else None


def parse_listing(html: str) -> tuple[list[dict[str, Any]], int | None]:
    """Cartoes de uma pagina (e o total declarado). Cartao sem link/id valido e descartado."""
    soup = make_soup(html)
    cards = soup.select('li[data-qa-id="job-card"]')
    declared = count_hint(soup.get_text(" ", strip=True))
    items: list[dict[str, Any]] = []
    for card in cards:
        link = card.select_one('a[data-qa-id="job-card-title"]')
        href = link.get("href") if link is not None else None
        if link is None or not isinstance(href, str):
            continue
        parts = urlsplit(href)
        match = _ID_RE.match(parts.path)
        title = clean(link.get_text(" ", strip=True))
        if not match or not title:
            continue
        items.append(
            {
                "id": match.group(1),
                "title": title,
                "url": f"{parts.scheme}://{parts.netloc}{parts.path}",
                "company": _text(card, "job-card-org"),
                "location": _text(card, "job-card-location"),
                "type": _text(card, "job-card-type"),
                "pay": _text(card, "job-card-pay"),
            }
        )
    return items, declared


@register("hireology")
class HireologyConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = HireologyConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "hireology.json"
    config: HireologyConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, HireologyConfig)
        return config.slug

    @property
    def _url(self) -> str:
        return f"{ORIGIN}/{self.config.slug}"

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, Any] = {"declared": None, "dropped": 0}

        async def fetch_page(page: int) -> list[dict[str, Any]]:
            html = await get_text(self.http, self._url, params={"page": str(page)})
            items, declared = parse_listing(html)
            if page == 1:
                state["declared"] = declared
                if not items and declared:
                    raise ParseError(f"Hireology: {declared} vagas declaradas, 0 cartoes lidos")
            return items

        result = await paginate_pages(
            fetch_page, page_size=PAGE_SIZE, max_pages=MAX_PAGES, key=lambda i: i["id"]
        )
        declared = state["declared"]
        complete = len(result.items) >= declared if isinstance(declared, int) else result.complete
        jobs = [RawJob(external_id=i["id"], payload=i) for i in result.items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        url = clean(p.get("url"))
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("company")),
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("type")),
            workplace_type=workplace(location, title),
            **parsed_salary_fields(parse_salary(clean(p.get("pay")))),
        )

    async def health_check(self) -> bool:
        try:
            parse_listing(await get_text(self.http, self._url))
        except (FetchError, ParseError):
            return False
        return True
