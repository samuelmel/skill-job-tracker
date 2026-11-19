"""Conector Paylocity Recruiting (pagina publica `recruiting.paylocity.com`).

GET https://recruiting.paylocity.com/recruiting/jobs/All/{guid} embute a lista completa em
`window.pageData = {...}` (`Jobs`). O guid do modulo da empresa e identificador publico (esta
na URL da pagina de carreiras). `Description` da listagem e apenas um trecho truncado, por isso
a descricao fica None; vagas `IsInternal` sao ignoradas (nao sao publicas).
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import get_text
from vaya_indexer.connectors._common import as_dict, clean, join_parts, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime

_PAGE_DATA = re.compile(r"window\.pageData\s*=\s*(\{.*?\})\s*;\s*\n", re.S)
GUID_PATTERN = r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"


class PaylocityConfig(BaseModel):
    guid: str = Field(pattern=GUID_PATTERN, description="GUID na URL /recruiting/jobs/All/{guid}")


def parse_jobs(html: str) -> list[Any]:
    match = _PAGE_DATA.search(html)
    if match is None:
        raise ParseError("Pagina Paylocity sem window.pageData")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise ParseError("window.pageData do Paylocity invalido") from exc
    jobs = data.get("Jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise ParseError("window.pageData do Paylocity sem lista Jobs")
    return jobs


@register("paylocity")
class PaylocityConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = PaylocityConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "paylocity.json"
    config: PaylocityConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, PaylocityConfig)
        return config.guid.lower()

    @property
    def _url(self) -> str:
        return f"https://recruiting.paylocity.com/recruiting/jobs/All/{self.config.guid}"

    async def fetch_jobs(self) -> FetchResult:
        items = parse_jobs(await get_text(self.http, self._url))
        jobs: list[RawJob] = []
        complete = True
        for item in items:
            if not isinstance(item, dict) or item.get("JobId") is None:
                complete = False
                continue
            if item.get("IsInternal"):
                continue
            jobs.append(RawJob(external_id=str(item["JobId"]), payload=item))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("JobTitle")) or ""
        place = as_dict(p.get("JobLocation"))
        location = clean(p.get("LocationName")) or join_parts(
            place.get("City"), place.get("State"), place.get("Country")
        )
        url = f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{raw.external_id}"
        remote = p.get("IsRemote")
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("PublishedDate"))),
            workplace_type=workplace(location, title, "remote" if remote is True else None),
        )

    async def health_check(self) -> bool:
        try:
            parse_jobs(await get_text(self.http, self._url))
        except FetchError:
            return False
        return True
