"""Conector Oracle Recruiting Cloud / Candidate Experience (REST publico das paginas de carreiras).

GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions com
`finder=findReqs;siteNumber={site},limit=N,offset=M`. A resposta traz um unico item de busca
cujo `requisitionList` e a pagina de vagas e `TotalJobsCount` o total. A listagem nao traz
descricao completa (so `ShortDescriptionStr`, um resumo), usada como descricao parcial.
Tenants atras de WAF respondem 403: e falha real, nao lista vazia.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN
from vaya_indexer.connectors._common import as_dict, clean, join_parts, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.pagination import paginate
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.normalization import parse_iso_datetime

PAGE_SIZE = 50
MAX_PAGES = 200
_WORKPLACE = {"ORA_ON_SITE": "onsite", "ORA_REMOTE": "remote", "ORA_HYBRID": "hybrid"}


class OracleRecruitingConfig(BaseModel):
    host: str = Field(
        min_length=1, pattern=HOST_PATTERN, description="ex.: acme.fa.us2.oraclecloud.com"
    )
    site_number: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$", description="ex.: CX_1")


@register("oracle_recruiting")
class OracleRecruitingConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = OracleRecruitingConfig
    fixture_path: ClassVar[Path | None] = (
        Path(__file__).with_name("fixtures") / "oracle_recruiting.json"
    )
    config: OracleRecruitingConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, OracleRecruitingConfig)
        return f"{config.host}/{config.site_number}"

    @property
    def _url(self) -> str:
        return f"https://{self.config.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    async def _page(self, offset: int) -> tuple[list[dict[str, Any]], int | None]:
        finder = f"findReqs;siteNumber={self.config.site_number},limit={PAGE_SIZE},offset={offset}"
        data = await self.http.get_json(
            self._url,
            params={
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations",
                "finder": finder,
            },
        )
        items = as_dict(data).get("items")
        if not isinstance(items, list) or not items:
            raise ParseError(f"Resposta inesperada do Oracle Recruiting em {self._url}")
        search = as_dict(items[0])
        reqs = search.get("requisitionList")
        if not isinstance(reqs, list):
            raise ParseError(f"Resposta do Oracle Recruiting sem requisitionList em {self._url}")
        total = search.get("TotalJobsCount")
        return [r for r in reqs if isinstance(r, dict)], total if isinstance(total, int) else None

    async def fetch_jobs(self) -> FetchResult:
        state: dict[str, int | None] = {"total": None}

        async def fetch_page(offset: int) -> list[dict[str, Any]]:
            reqs, total = await self._page(offset)
            state["total"] = total
            return reqs

        # Paginas intermediarias podem vir com menos itens que o limite (ex.: 49 de 50):
        # so uma pagina vazia encerra, nunca uma pagina curta.
        result = await paginate(
            fetch_page, step=PAGE_SIZE, max_pages=MAX_PAGES, key=lambda r: str(r.get("Id"))
        )
        items = [r for r in result.items if r.get("Id") is not None]
        total = state["total"]
        complete = (
            result.complete
            and len(items) == len(result.items)
            and (total is None or len(items) >= total)
        )
        jobs = [RawJob(external_id=str(r["Id"]), payload=r) for r in items]
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("Title")) or ""
        locations = [p.get("PrimaryLocation")]
        for extra in p.get("secondaryLocations") or []:
            locations.append(as_dict(extra).get("Name"))
        location = join_parts(*locations, sep="; ")
        url = (
            f"https://{self.config.host}/hcmUI/CandidateExperience/en/sites/"
            f"{self.config.site_number}/job/{raw.external_id}"
        )
        posted = clean(p.get("PostedDate"))
        explicit = _WORKPLACE.get(str(p.get("WorkplaceTypeCode") or "").upper())
        return NormalizedJob(
            title=title,
            description=clean(p.get("ShortDescriptionStr")),
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(posted) if posted else None,
            workplace_type=workplace(location, title, explicit),
        )

    async def health_check(self) -> bool:
        try:
            await self._page(0)
        except FetchError:
            return False
        return True
