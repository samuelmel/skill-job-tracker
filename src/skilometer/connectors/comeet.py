"""Conector Comeet (Careers API publica `careers-api/2.0`).

O token e o do widget publico da pagina de carreiras da propria empresa: nao fica no codigo
nem na configuracao; e lido em tempo de execucao do HTML de `careers_url` (que contem a URL
`.../careers-api/2.0/company/{uid}/positions?token=...`). Sem o token na pagina, falha com
ParseError em vez de adivinhar.
"""

import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from skilometer.connectors._ats2a import get_text
from skilometer.connectors._common import as_dict, clean, employment, join_parts, workplace
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError, ParseError

API_BASE = "https://www.comeet.co/careers-api/2.0/company"


class ComeetConfig(BaseModel):
    company_uid: str = Field(pattern=r"^[0-9A-Za-z]+\.[0-9A-Za-z]+$", description="ex.: 43.001")
    careers_url: str = Field(
        pattern=r"^https://[^\s]+$", description="pagina publica de carreiras com o widget Comeet"
    )


def extract_token(html: str, company_uid: str) -> str | None:
    uid = re.escape(company_uid)
    pattern = rf"careers-api/2\.0/company/{uid}/positions[^\"'\s?]*\?token=([A-Za-z0-9]+)"
    match = re.search(pattern, html)
    return match.group(1) if match else None


@register("comeet")
class ComeetConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = ComeetConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "comeet.json"
    config: ComeetConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, ComeetConfig)
        return config.company_uid

    async def _positions(self, details: bool) -> list[Any]:
        html = await get_text(self.http, self.config.careers_url)
        token = extract_token(html, self.config.company_uid)
        if token is None:
            raise ParseError(f"Token publico do Comeet nao encontrado em {self.config.careers_url}")
        params = {"token": token, **({"details": "true"} if details else {})}
        url = f"{API_BASE}/{self.config.company_uid}/positions"
        data = await self.http.get_json(url, params=params)
        if not isinstance(data, list):
            raise ParseError(f"Resposta inesperada do Comeet em {url}")
        return data

    async def fetch_jobs(self) -> FetchResult:
        jobs: list[RawJob] = []
        complete = True
        for item in await self._positions(details=True):
            if not isinstance(item, dict) or not item.get("uid"):
                complete = False
                continue
            jobs.append(RawJob(external_id=str(item["uid"]), payload=item))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("name")) or ""
        loc = as_dict(p.get("location"))
        location = clean(loc.get("name")) or join_parts(loc.get("city"), loc.get("country"))
        explicit = clean(p.get("workplace_type"))
        if explicit is None and loc.get("is_remote") is True:
            explicit = "remote"
        blocks = p.get("details")
        parts = [
            b["value"]
            for b in (blocks if isinstance(blocks, list) else [])
            if isinstance(b, dict) and isinstance(b.get("value"), str) and b["value"].strip()
        ]
        url = clean(p.get("url_active_page")) or clean(p.get("url_comeet_hosted_page"))
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("company_name")),
            description="\n".join(parts) or None,
            location=location,
            original_url=url,
            application_url=url,
            employment_type=employment(p.get("employment_type")),
            workplace_type=workplace(location, title, explicit),
        )

    async def health_check(self) -> bool:
        try:
            await self._positions(details=False)
        except (FetchError, ParseError):
            return False
        return True
