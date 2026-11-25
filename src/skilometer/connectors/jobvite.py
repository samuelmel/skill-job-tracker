"""Conector Jobvite (HTML server-side de `jobs.jobvite.com/{company}/jobs`).

A pagina publica lista todas as vagas em tabelas `table.jv-job-list` (uma por departamento),
com `td.jv-job-list-name > a[href=/{company}/job/{id}]` e `td.jv-job-list-location`.
Empresa inexistente redireciona (200) para a pagina de suporte do Jobvite e paginas com layout
proprio carregam a lista por JavaScript: em ambos os casos nao ha tabela e o resultado e
ParseError, nunca uma lista vazia "completa". A descricao so existe no detalhe (fica None).
"""

import re
from pathlib import Path
from typing import ClassVar

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import get_text
from skilometer.connectors._common import SLUG_PATTERN, clean, workplace
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

_JOB_ID = re.compile(r"/job/([A-Za-z0-9]+)")


class JobviteConfig(BaseModel):
    company: str = Field(
        min_length=1, pattern=SLUG_PATTERN, description="jobs.jobvite.com/{company}"
    )


def parse_jobs(html: str) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.jv-job-list")
    if not tables:
        raise ParseError("Pagina Jobvite sem tabela de vagas (empresa inexistente ou lista via JS)")
    jobs: list[dict[str, str | None]] = []
    for row in soup.select("table.jv-job-list tr"):
        link = row.select_one("td.jv-job-list-name a[href]")
        if link is None:
            continue
        href = str(link.get("href"))
        match = _JOB_ID.search(href)
        if match is None:
            continue
        location = row.select_one("td.jv-job-list-location")
        jobs.append(
            {
                "id": match.group(1),
                "title": link.get_text(" ", strip=True),
                "href": href,
                "location": " ".join(location.get_text().split()) or None if location else None,
            }
        )
    return jobs


@register("jobvite")
class JobviteConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = JobviteConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "jobvite.json"
    config: JobviteConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, JobviteConfig)
        return config.company

    @property
    def _url(self) -> str:
        return f"https://jobs.jobvite.com/{self.config.company}/jobs"

    async def fetch_jobs(self) -> FetchResult:
        rows = parse_jobs(await get_text(self.http, self._url))
        seen: dict[str, RawJob] = {}
        for row in rows:
            seen.setdefault(str(row["id"]), RawJob(external_id=str(row["id"]), payload=dict(row)))
        return FetchResult(jobs=list(seen.values()), complete=True)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        href = clean(p.get("href")) or f"/{self.config.company}/job/{raw.external_id}"
        url = href if href.startswith("http") else f"https://jobs.jobvite.com{href}"
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            parse_jobs(await get_text(self.http, self._url))
        except FetchError:
            return False
        return True
