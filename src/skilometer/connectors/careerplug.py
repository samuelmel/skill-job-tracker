"""Conector CareerPlug (HTML server-side com JSON-LD schema.org em `/jobs`).

GET https://{subdomain}.careerplug.com/jobs traz um `ItemList` de `JobPosting`. `/jobs.js` e
`/j/` sao proibidos pelo robots.txt e nao sao usados. A listagem so vale se o `ItemList` existe
ou a pagina declara que nao ha vagas; qualquer outra pagina (login, vaga unica) e ParseError,
nunca uma lista vazia "completa".
"""

import json
from pathlib import Path
from typing import Any, ClassVar

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import get_text
from skilometer.connectors._common import SUBDOMAIN_PATTERN, as_dict, clean
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.jsonld import job_posting_to_normalized
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

EMPTY_MARKER = "there are no jobs posted at this time"


class CareerPlugConfig(BaseModel):
    subdomain: str = Field(min_length=1, pattern=SUBDOMAIN_PATTERN)


def _is_posting(node: Any) -> bool:
    kind = as_dict(node).get("@type")
    return "JobPosting" in (kind if isinstance(kind, list) else [kind])


def parse_listing(html: str) -> tuple[list[dict[str, Any]], int | None, bool]:
    """(vagas, numberOfItems declarado, pagina_tem_ItemList) a partir dos blocos JSON-LD."""
    postings: list[dict[str, Any]] = []
    declared: int | None = None
    found_list = False
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text() or "", strict=False)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if _is_posting(node):
                postings.append(node)
            elif as_dict(node).get("@type") == "ItemList":
                found_list = True
                count = node.get("numberOfItems")
                declared = count if isinstance(count, int) else declared
                elements = node.get("itemListElement")
                for element in elements if isinstance(elements, list) else []:
                    item = as_dict(element).get("item")
                    if isinstance(item, dict) and _is_posting(item):
                        postings.append(item)
    return postings, declared, found_list


def _job_id(posting: dict[str, Any]) -> str | None:
    value = as_dict(posting.get("identifier")).get("value")
    return str(value) if isinstance(value, str | int) and str(value) else None


@register("careerplug")
class CareerPlugConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = CareerPlugConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "careerplug.json"
    config: CareerPlugConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, CareerPlugConfig)
        return config.subdomain

    @property
    def _origin(self) -> str:
        return f"https://{self.config.subdomain}.careerplug.com"

    async def _listing(self) -> tuple[list[dict[str, Any]], int | None]:
        html = await get_text(self.http, f"{self._origin}/jobs")
        postings, declared, found_list = parse_listing(html)
        if not found_list:
            if EMPTY_MARKER in BeautifulSoup(html, "lxml").get_text(" ").casefold():
                return [], 0
            raise ParseError(f"Pagina {self._origin}/jobs sem ItemList de vagas")
        return postings, declared

    async def fetch_jobs(self) -> FetchResult:
        postings, declared = await self._listing()
        jobs: list[RawJob] = []
        complete = declared is None or declared == len(postings)
        for posting in postings:
            job_id = _job_id(posting)
            if job_id is None:
                complete = False
                continue
            jobs.append(RawJob(external_id=job_id, payload=posting))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        job = job_posting_to_normalized(raw.payload)
        url = clean(raw.payload.get("url")) or f"{self._origin}/jobs/{raw.external_id}"
        return job.model_copy(update={"original_url": url, "application_url": url})

    async def health_check(self) -> bool:
        try:
            await self._listing()
        except FetchError:
            return False
        return True
