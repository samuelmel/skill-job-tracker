"""Conector Avature (portais de carreiras `{host}/{path}/SearchJobs`).

Lista via feed RSS publico `.../SearchJobs/feed/` (titulo, link, data e "local - ref" em
`description`), lido com o helper `feeds.parse_feed`. O feed e limitado pelo portal e nao diz
o total; por isso o total real e lido do "N-M of T" da primeira pagina HTML `SearchJobs`, e a
listagem so e `complete` quando o numero de vagas unicas do feed iguala T. Sem total legivel,
`complete=False`. Descricao so existe no detalhe (fica None). O robots.txt libera `/main` e
`/*/main` (so proibe URLs com `qtvc=`).
"""

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vaya_indexer.connectors._ats2a import HOST_PATTERN, PORTAL_PATH_PATTERN, get_text
from vaya_indexer.connectors._common import clean, workplace
from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.feeds import parse_feed
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError
from vaya_indexer.processing.normalization import parse_iso_datetime

_TOTAL = re.compile(r"\d+\s*-\s*\d+\s+of\s+(\d+)")
_JOB_ID = re.compile(r"/JobDetail/[^?#]*?/(\d+)(?:[/?#]|$)")
_TRAILING_REF = re.compile(r"\s+-\s+[\w.-]*\d[\w.-]*$")


class AvatureConfig(BaseModel):
    host: str = Field(min_length=1, pattern=HOST_PATTERN, description="ex.: careers.avature.net")
    path: str = Field(min_length=1, pattern=PORTAL_PATH_PATTERN, description="ex.: en_US/main")


@register("avature")
class AvatureConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = AvatureConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "avature.json"
    config: AvatureConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, AvatureConfig)
        return f"{config.host}/{config.path}"

    @property
    def _base(self) -> str:
        return f"https://{self.config.host}/{self.config.path}/SearchJobs"

    async def _total(self) -> int | None:
        match = _TOTAL.search(" ".join((await get_text(self.http, self._base)).split()))
        return int(match.group(1)) if match else None

    async def fetch_jobs(self) -> FetchResult:
        entries = parse_feed(await get_text(self.http, f"{self._base}/feed/"))
        jobs: dict[str, RawJob] = {}
        complete = True
        for entry in entries:
            match = _JOB_ID.search(entry.link or "")
            if match is None:
                complete = False
                continue
            jobs.setdefault(
                match.group(1),
                RawJob(
                    external_id=match.group(1),
                    payload={
                        "title": entry.title,
                        "link": entry.link,
                        "summary": entry.summary,
                        "published": entry.published.isoformat() if entry.published else None,
                    },
                ),
            )
        total = await self._total()
        if total is None or total != len(jobs):
            complete = False
        return FetchResult(jobs=list(jobs.values()), complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        summary = clean(p.get("summary"))
        location = clean(_TRAILING_REF.sub("", summary)) if summary else None
        url = clean(p.get("link"))
        return NormalizedJob(
            title=title,
            location=location,
            original_url=url,
            application_url=url,
            original_posted_at=parse_iso_datetime(clean(p.get("published"))),
            workplace_type=workplace(location, title),
        )

    async def health_check(self) -> bool:
        try:
            parse_feed(await get_text(self.http, f"{self._base}/feed/"))
        except FetchError:
            return False
        return True
