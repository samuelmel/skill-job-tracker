"""Conector WP Job Manager (feed RSS `?feed=job_feed` de sites WordPress com o plugin).

GET {base_url}/?feed=job_feed&posts_per_page=100&paged=N: RSS 2.0 com extensoes `job_listing:*`
(location, company, job_type, job_category, salary). Paginacao por `paged`: termina numa pagina
vazia ou com menos de PAGE_SIZE itens; pagina que repete itens (sites que ignoram `paged`, como
o jobicy.com) interrompe com complete=False. No maximo MAX_PAGES paginas por coleta (excedente ->
complete=False). Item sem titulo/id, ou erro numa pagina apos a primeira -> complete=False.
O feed so inclui vagas com tipo definido e nao traz o link externo de candidatura (o formulario
fica na pagina da vaga), entao `application_url` e o `link` do item. O namespace XML
`job_listing` e o proprio site (varia por instalacao): as extensoes sao lidas pelo prefixo.
Os sites sao WordPress auto-hospedados: `base_url` e a identidade do board (https, sem query).
"""

import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from skilometer.connectors._ats2a import HOST_PATTERN
from skilometer.connectors._common import clean, employment, parsed_salary_fields, workplace
from skilometer.connectors._web import get_bytes, rss_entries
from skilometer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from skilometer.connectors.pagination import paginate_pages
from skilometer.connectors.registry import register
from skilometer.core.exceptions import FetchError
from skilometer.processing.normalization import parse_iso_datetime
from skilometer.processing.salary import parse_salary
from skilometer.processing.text import html_to_text

PAGE_SIZE = 100
MAX_PAGES = 20
_PATH = re.compile(r"^(/[A-Za-z0-9_~-][A-Za-z0-9_.~-]*)*/?$")
_EXTENSIONS = ("location", "company", "job_type", "job_category", "salary")


class WpJobManagerConfig(BaseModel):
    base_url: str = Field(
        min_length=1, description="URL base do site WordPress, ex.: https://jobs.blog/acrejobs"
    )

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or not re.fullmatch(HOST_PATTERN, host)
            or parts.port is not None
            or parts.username is not None
            or parts.query
            or parts.fragment
            or not _PATH.fullmatch(parts.path)
        ):
            raise ValueError("base_url deve ser https://host[/caminho], sem query nem credenciais")
        return f"https://{host.lower()}{parts.path.rstrip('/')}"


def _extension(element: Any, name: str) -> str | None:
    """Texto do filho `job_listing:{name}` (o namespace e a URL do site, so o prefixo e fixo)."""
    for child in element:
        if (
            isinstance(child.tag, str)
            and child.prefix == "job_listing"
            and child.tag.rsplit("}", 1)[-1] == name
        ):
            return clean("".join(child.itertext()))
    return None


@register("wp-job-manager")
class WpJobManagerConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = WpJobManagerConfig
    fixture_path: ClassVar[Path | None] = (
        Path(__file__).with_name("fixtures") / "wp_job_manager.json"
    )
    config: WpJobManagerConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, WpJobManagerConfig)
        return config.base_url.removeprefix("https://")

    @property
    def _url(self) -> str:
        return f"{self.config.base_url}/"

    def _params(self, page: int) -> dict[str, str]:
        return {"feed": "job_feed", "posts_per_page": str(PAGE_SIZE), "paged": str(page)}

    async def fetch_jobs(self) -> FetchResult:
        dropped = 0
        page_failed = False

        async def fetch_page(page: int) -> list[RawJob]:
            nonlocal dropped, page_failed
            try:
                xml = await get_bytes(self.http, self._url, params=self._params(page))
            except FetchError:
                if page == 1:
                    raise
                page_failed = True  # pagina posterior falhou: mantem o que ja veio, incompleto
                return []
            entries, skipped = rss_entries(xml)
            dropped += skipped
            jobs: list[RawJob] = []
            for entry in entries:
                payload: dict[str, Any] = {
                    "guid": entry.id,
                    "title": entry.title,
                    "link": entry.link,
                    "description": entry.content or entry.summary,
                    "pubDate": entry.published.isoformat() if entry.published else None,
                    **{name: _extension(entry.element, name) for name in _EXTENSIONS},
                }
                jobs.append(RawJob(external_id=entry.id, payload=payload))
            return jobs

        result = await paginate_pages(
            fetch_page, page_size=PAGE_SIZE, max_pages=MAX_PAGES, key=lambda j: j.external_id
        )
        complete = result.complete and dropped == 0 and not page_failed
        return FetchResult(jobs=result.items, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        title = clean(p.get("title")) or ""
        location = clean(p.get("location"))
        link = clean(p.get("link"))
        description = p.get("description")
        return NormalizedJob(
            title=title,
            company_name=clean(p.get("company")),
            description=html_to_text(description) if isinstance(description, str) else None,
            location=location,
            original_url=link,
            application_url=link,
            employment_type=employment(p.get("job_type")),
            workplace_type=workplace(location, title),
            original_posted_at=parse_iso_datetime(clean(p.get("pubDate"))),
            **parsed_salary_fields(parse_salary(clean(p.get("salary")))),
        )

    async def health_check(self) -> bool:
        try:
            rss_entries(await get_bytes(self.http, self._url, params=self._params(1)))
        except FetchError:
            return False
        return True
