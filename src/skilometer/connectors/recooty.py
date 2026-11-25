"""Conector Recooty (HTML do portal publico + JSON-LD JobPosting das paginas de vaga).

1. GET https://jobs.recooty.com/{company}?page=N: cartoes com titulo (`h3 a`), local, tipo e
   senioridade (identificados pelos icones fa-location-arrow, fa-briefcase, fa-business-time);
   10 vagas por pagina, ate uma pagina curta ou vazia.
2. GET de cada /{company}/{slug} para ler o `JobPosting` (descricao, data, local).
Nao ha API nem feed (/feed/ retorna 404); o robots.txt libera tudo. Seletores por classe CSS
sao frageis: se o layout mudar a coleta falha (ParseError) ou vem incompleta. Detalhe que
falhar mantem a vaga so com os dados da listagem (complete=False). No maximo MAX_JOBS vagas.
"""

import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from skilometer.connectors._common import SLUG_PATTERN, clean, employment, workplace
from skilometer.connectors._web import gather_details, get_text, make_soup
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from skilometer.connectors.pagination import paginate_pages
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

ORIGIN = "https://jobs.recooty.com"
PAGE_SIZE = 10
MAX_PAGES = 30
MAX_JOBS = 200
_ICONS = {"location": "fa-location-arrow", "type": "fa-briefcase", "seniority": "fa-business-time"}


class RecootyConfig(BaseModel):
    company: str = Field(min_length=1, pattern=SLUG_PATTERN, description="jobs.recooty.com/{co}")


def _item_text(block: Any, icon: str) -> str | None:
    marker = block.select_one(f"li i.{icon}")
    item = marker.find_parent("li") if marker is not None else None
    if item is None:
        return None
    text = clean(item.get_text(" ", strip=True))
    return clean(text.strip("[]")) if text else None


def parse_listing(html: str, company: str) -> list[dict[str, Any]]:
    """Cartoes de uma pagina; os de outra empresa ou sem slug valido sao descartados."""
    soup = make_soup(html)
    items: list[dict[str, Any]] = []
    for link in soup.select("h3 a[href]"):
        href = link.get("href")
        title = clean(link.get_text(" ", strip=True))
        if not isinstance(href, str) or not title:
            continue
        parts = urlsplit(href)
        match = re.match(rf"^/{re.escape(company)}/([^/]+)/?$", parts.path)
        if parts.netloc != "jobs.recooty.com" or not match:
            continue
        block = link.find_parent("h3").parent  # type: ignore[union-attr]
        items.append(
            {
                "id": match.group(1),
                "title": title,
                "url": f"{ORIGIN}{parts.path}",
                "location": _item_text(block, _ICONS["location"]),
                "type": _item_text(block, _ICONS["type"]),
                "seniority": _item_text(block, _ICONS["seniority"]),
            }
        )
    return items


@register("recooty")
class RecootyConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = RecootyConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "recooty.json"
    config: RecootyConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, RecootyConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"{ORIGIN}/{self.config.company}"

    async def _detail(self, item: dict[str, Any]) -> dict[str, Any] | None:
        postings = extract_job_postings(await get_text(self.http, item["url"]))
        return postings[0] if postings else None

    async def fetch_jobs(self) -> FetchResult:
        async def fetch_page(page: int) -> list[dict[str, Any]]:
            html = await get_text(self.http, self._url, params={"page": str(page)})
            return parse_listing(html, self.config.company)

        result = await paginate_pages(
            fetch_page, page_size=PAGE_SIZE, max_pages=MAX_PAGES, key=lambda i: i["id"]
        )
        items = result.items[:MAX_JOBS]
        complete = result.complete and len(result.items) <= MAX_JOBS
        details = await gather_details(items, self._detail)
        jobs: list[RawJob] = []
        for item, posting in zip(items, details, strict=True):
            if posting is None:
                complete = False
            jobs.append(RawJob(external_id=item["id"], payload={**item, "posting": posting}))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        url = clean(p.get("url"))
        posting = p.get("posting")
        if isinstance(posting, dict) and posting:
            job = job_posting_to_normalized(posting)
            return job.model_copy(
                update={
                    "original_url": url,
                    "application_url": url,
                    "location": job.location or clean(p.get("location")),
                }
            )
        title = clean(p.get("title"))
        if not title:
            raise ParseError("Recooty: vaga sem titulo")
        location = clean(p.get("location"))
        return NormalizedJob(
            title=title,
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
