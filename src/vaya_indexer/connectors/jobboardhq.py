"""Conector jobBoardHQ (ASP.NET WebForms: lista `/search` paginada por postback + JSON-LD).

ATENCAO: `/search?page=N` NAO pagina (sempre devolve a pagina 1). A paginacao real e um postback
ASP.NET: GET `/search` (o URL FINAL, apos redirects, e o alvo do POST: postar no host sem `www`
vira GET e devolve a pagina 1), junta os `input[type=hidden]` de `form#form1` (`__VIEWSTATE`...)
e faz POST com `__EVENTTARGET=Page` e `__EVENTARGUMENT=N`. O primeiro POST de uma sessao costuma
devolver a pagina 1 de novo: confere-se `span.pagerCurrentPage` e repete-se (ate 3 vezes) com o
estado da ultima resposta. Para quando nao ha link `__doPostBack('Page','N+1')`.

Cartoes `div.listRow`: `a#lnkJobId` (href `/job/{id}/{slug}/...`) e `#companyLine` com
[empresa, '-', local, '-', MM/DD/YYYY]. A pagina de detalhe tem JSON-LD JobPosting (descricao,
datePosted, salario em alguns quadros); a empresa do cartao prevalece (o JSON-LD pode dizer
"Confidential Employer"). Os quadros limitam a busca a 500/1000 resultados: se a contagem declarada
for igual a coletada e multipla de 500, `complete=False`. Teto proprio de `MAX_JOBS`.
Sem sitemap/RSS; robots.txt dos quadros testados e vazio.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._common import clean, workplace
from vaya_indexer.connectors._web import count_hint, gather_details, get_text, make_soup
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.jsonld import extract_job_postings, job_posting_to_normalized
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

MAX_JOBS = 600
MAX_PAGES = 60
POST_ATTEMPTS = 3
_JOB_HREF = re.compile(r"^/job/([A-Za-z0-9]+)/")
_PAGE_LINK = re.compile(r"__doPostBack\('Page',\s*'(\d+)'\)")
_CURRENT = re.compile(r'pagerCurrentPage">(\d+)<')
_DATE = re.compile(r"\d+/\d+/\d{4}$")


class JobBoardHQConfig(BaseModel):
    board_domain: str = Field(min_length=1, pattern=HOST_PATTERN)


def parse_cards(html: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for row in make_soup(html).select("div.listRow"):
        link = row.select_one("a#lnkJobId")
        href = link.get("href") if link else None
        match = _JOB_HREF.match(href) if isinstance(href, str) else None
        if link is None or match is None or not isinstance(href, str):
            continue
        parts = [
            t
            for span in row.select("#companyLine span")
            if (t := span.get_text(" ", strip=True)) and t != "-"
        ]
        has_date = len(parts) > 1 and _DATE.match(parts[-1]) is not None
        cards.append(
            {
                "id": match.group(1),
                "href": href,
                "title": link.get_text(" ", strip=True),
                "company": parts[0] if parts else None,
                "location": parts[1] if len(parts) > 2 else None,
                "date": parts[-1] if has_date else None,
            }
        )
    return cards


def _hidden_fields(html: str) -> dict[str, str]:
    form = make_soup(html).find("form", id="form1")
    if form is None:
        raise ParseError("jobBoardHQ: form#form1 ausente na pagina de busca")
    return {
        str(i["name"]): str(i.get("value", ""))
        for i in form.find_all("input", type="hidden")
        if i.get("name")
    }


def _parse_card_date(value: Any) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").replace(tzinfo=UTC)
    except ValueError:
        return None


@register("jobboardhq")
class JobBoardHQConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JobBoardHQConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jobboardhq.json"
    config: JobBoardHQConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JobBoardHQConfig)
        return config.board_domain

    async def _post_page(self, target: str, html: str, page: int) -> str | None:
        """HTML da pagina `page` (None se nao foi atingida apos POST_ATTEMPTS)."""
        for _ in range(POST_ATTEMPTS):
            data = {**_hidden_fields(html), "__EVENTTARGET": "Page", "__EVENTARGUMENT": str(page)}
            try:
                response = await self.http.request("POST", target, data=data)
            except FetchError:
                return None
            html = response.text
            current = _CURRENT.search(html)
            if current and int(current.group(1)) == page:
                return html
        return None

    async def _collect_cards(self) -> tuple[list[dict[str, Any]], str, bool]:
        """(cartoes, origem final, listagem integral)."""
        first = await self.http.request("GET", f"https://{self.config.board_domain}/search")
        target = str(first.url)
        parts = urlsplit(target)
        origin = f"{parts.scheme}://{parts.netloc}"
        html = first.text
        declared = count_hint(make_soup(html).get_text(" ", strip=True))
        cards = parse_cards(html)
        if not cards:
            raise ParseError("jobBoardHQ: pagina de busca sem vagas reconhecidas")
        seen = {c["id"] for c in cards}
        complete = True
        page = 1
        while True:
            if len(seen) >= MAX_JOBS or page >= MAX_PAGES:
                complete = not _has_page(html, page + 1) and len(seen) < MAX_JOBS
                break
            page += 1
            if not _has_page(html, page):
                break
            fetched = await self._post_page(target, html, page)
            if fetched is None:
                complete = False
                break
            html = fetched
            for card in parse_cards(html):
                if card["id"] not in seen:
                    seen.add(card["id"])
                    cards.append(card)
        if len(cards) > MAX_JOBS:
            cards, complete = cards[:MAX_JOBS], False
        if complete and declared is not None and declared == len(cards) and declared % 500 == 0:
            complete = False  # teto de 500/1000 resultados do proprio quadro
        return cards, origin, complete

    async def fetch_jobs(self) -> FetchResult:
        cards, origin, complete = await self._collect_cards()

        async def detail(card: dict[str, Any]) -> dict[str, Any] | None:
            postings = extract_job_postings(await get_text(self.http, origin + card["href"]))
            return postings[0] if postings else None

        postings = await gather_details(cards, detail)
        jobs: list[RawJob] = []
        for card, posting in zip(cards, postings, strict=True):
            if posting is None:
                complete = False  # detalhe falhou: fica so com os dados do cartao
            payload: dict[str, Any] = {**card, "url": origin + card["href"], "posting": posting}
            jobs.append(RawJob(external_id=card["id"], payload=payload))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        posting = p.get("posting")
        url = clean(p.get("url"))
        company, location = clean(p.get("company")), clean(p.get("location"))
        card_date = _parse_card_date(p.get("date"))
        if isinstance(posting, dict):
            job = job_posting_to_normalized(posting)
            update: dict[str, Any] = {"original_url": url, "application_url": url}
            if company:
                update["company_name"] = company
            if job.original_posted_at is None:
                update["original_posted_at"] = card_date
            if job.location is None and location:
                update["location"] = location
            return job.model_copy(update=update)
        title = clean(p.get("title"))
        if not title:
            raise ParseError("jobBoardHQ: vaga sem titulo")
        return NormalizedJob(
            title=title,
            company_name=company,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=card_date,
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            html = await get_text(self.http, f"https://{self.config.board_domain}/search")
        except FetchError:
            return False
        return bool(parse_cards(html))


def _has_page(html: str, page: int) -> bool:
    return page in {int(n) for n in _PAGE_LINK.findall(html)}
