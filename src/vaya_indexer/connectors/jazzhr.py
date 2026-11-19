"""Conector JazzHR (HTML server-side de `{subdomain}.applytojob.com/apply`).

A pagina publica lista as vagas em `li.list-group-item` (titulo/link em `h3 a`, local no `li`
com o icone `fa-map-marker`). robots.txt so proibe `/cb`. Subdominio inexistente redireciona
(200) para o site institucional da JazzHR; sem redirecionamento e sem vagas listadas a pagina
nao e uma listagem valida e o resultado e ParseError, nunca uma lista vazia "completa".
Descricao so existe no detalhe (fica None).
"""

import re
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from vaya_indexer.connectors._common import SUBDOMAIN_PATTERN, clean, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError

_JOB_ID = re.compile(r"/apply/([A-Za-z0-9]+)(?:/|$)")


class JazzHRConfig(BaseModel):
    subdomain: str = Field(
        min_length=1, pattern=SUBDOMAIN_PATTERN, description="{sub}.applytojob.com"
    )


def parse_jobs(html: str) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[dict[str, str | None]] = []
    for item in soup.select("li.list-group-item"):
        link = item.select_one("h3 a[href]")
        if link is None:
            continue
        href = str(link.get("href"))
        match = _JOB_ID.search(href)
        if match is None:
            continue
        place = item.select_one("li i.fa-map-marker")
        location = place.parent.get_text(" ", strip=True) if place and place.parent else None
        jobs.append(
            {
                "id": match.group(1),
                "title": link.get_text(" ", strip=True),
                "url": href,
                "location": location,
            }
        )
    return jobs


@register("jazzhr")
class JazzHRConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JazzHRConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jazzhr.json"
    config: JazzHRConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JazzHRConfig)
        return config.subdomain

    @property
    def _url(self) -> str:
        return f"https://{self.config.subdomain}.applytojob.com/apply"

    async def _listing(self) -> list[dict[str, str | None]]:
        response = await self.http.request("GET", self._url)
        host = urlparse(str(response.url)).hostname or ""
        if not host.endswith(".applytojob.com"):
            raise ParseError(f"{self._url} redirecionou para {host}: subdominio JazzHR inexistente")
        jobs = parse_jobs(response.text)
        if not jobs:
            raise ParseError(
                f"Pagina {self._url} sem vagas listadas (layout desconhecido ou vazia)"
            )
        return jobs

    async def fetch_jobs(self) -> FetchResult:
        seen: dict[str, RawJob] = {}
        for row in await self._listing():
            seen.setdefault(str(row["id"]), RawJob(external_id=str(row["id"]), payload=dict(row)))
        return FetchResult(jobs=list(seen.values()), complete=True)

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
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            await self._listing()
        except FetchError:
            return False
        return True
