"""Conector Greenhouse (Job Board API publica).

Docs: https://developers.greenhouse.io/job-board.html
"""

import html
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import register
from vaya_indexer.core.exceptions import FetchError, ParseError
from vaya_indexer.processing.attributes import infer_workplace_type, normalize_employment_type
from vaya_indexer.processing.normalization import normalize_whitespace, parse_iso_datetime
from vaya_indexer.processing.salary import ParsedSalary, parse_salary

API_BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseConfig(BaseModel):
    board_token: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    api_base_url: str | None = Field(
        default=None, description="Base alternativa da API (testes/demo); padrao: API oficial"
    )

    @field_validator("api_base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("api_base_url deve ser uma URL http(s) valida")
        return value.rstrip("/")


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = normalize_whitespace(value)
    return cleaned or None


_EMPLOYMENT_KEYS = ("employment type", "job type", "contract type", "worker type")
_WORKPLACE_KEYS = ("workplace", "work type", "location type", "remote", "work model")
_SALARY_KEYS = ("salary", "compensation", "pay range", "base pay")


def _metadata_values(metadata: Any) -> dict[str, str]:
    """Campos `metadata` do Greenhouse (nome -> texto); so valores textuais explicitos."""
    values: dict[str, str] = {}
    if not isinstance(metadata, list):
        return values
    for item in metadata:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        value = item.get("value")
        if isinstance(value, list):
            value = ", ".join(v for v in value if isinstance(v, str))
        if isinstance(value, str) and value.strip():
            values[item["name"].casefold()] = value
    return values


def _first(metadata: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for name, value in metadata.items():
        if any(key in name for key in keys):
            return value
    return None


def _salary(payload: dict[str, Any], metadata: dict[str, str]) -> ParsedSalary | None:
    """Salario so de texto explicito: metadata de remuneracao ou titulo/blurb de pay_input_ranges.

    Os centavos de `pay_input_ranges` nao trazem periodo, entao nao sao usados sozinhos.
    """
    candidates = [v for k, v in metadata.items() if any(key in k for key in _SALARY_KEYS)]
    ranges = payload.get("pay_input_ranges")
    if isinstance(ranges, list):
        for item in ranges:
            if isinstance(item, dict):
                candidates += [item[k] for k in ("blurb", "title") if isinstance(item.get(k), str)]
    for text in candidates:
        if (parsed := parse_salary(text)) is not None:
            return parsed
    return None


@register("greenhouse")
class GreenhouseConnector(BaseConnector):
    config_model: ClassVar[type[BaseModel]] = GreenhouseConfig
    fixture_path: ClassVar[Path | None] = Path(__file__).with_name("fixtures") / "greenhouse.json"
    config: GreenhouseConfig

    @classmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        assert isinstance(config, GreenhouseConfig)
        return config.board_token

    @property
    def _board_url(self) -> str:
        return f"{self.config.api_base_url or API_BASE}/{self.config.board_token}"

    @property
    def _jobs_url(self) -> str:
        return f"{self._board_url}/jobs"

    async def fetch_jobs(self) -> FetchResult:
        data = await self.http.get_json(self._jobs_url, params={"content": "true"})
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ParseError(f"Resposta inesperada do Greenhouse em {self._jobs_url}")
        jobs: list[RawJob] = []
        for item in data["jobs"]:
            if not isinstance(item, dict) or item.get("id") is None:
                continue  # sem id nao ha como identificar; listagem passa a ser incompleta
            jobs.append(RawJob(external_id=str(item["id"]), payload=item))
        total = (data.get("meta") or {}).get("total")
        complete = len(jobs) == len(data["jobs"]) and (total is None or total == len(jobs))
        return FetchResult(jobs=jobs, complete=complete)

    def normalize(self, raw: RawJob) -> NormalizedJob:
        p = raw.payload
        location = p.get("location")
        content = p.get("content")
        title = _clean(p.get("title")) or ""
        location_name = _clean(location.get("name")) if isinstance(location, dict) else None
        metadata = _metadata_values(p.get("metadata"))
        employment = normalize_employment_type(_first(metadata, _EMPLOYMENT_KEYS))
        workplace = infer_workplace_type(location_name, title, _first(metadata, _WORKPLACE_KEYS))
        salary = _salary(p, metadata)
        return NormalizedJob(
            title=title,
            company_name=_clean(p.get("company_name")),
            description=html.unescape(content) if isinstance(content, str) and content else None,
            location=location_name,
            original_url=_clean(p.get("absolute_url")),
            application_url=_clean(p.get("absolute_url")),
            original_posted_at=parse_iso_datetime(p.get("first_published")),
            employment_type=employment.value if employment else None,
            workplace_type=workplace.value if workplace else None,
            salary_min=salary.min if salary else None,
            salary_max=salary.max if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period.value if salary else None,
        )

    async def health_check(self) -> bool:
        try:
            data = await self.http.get_json(self._board_url)
        except FetchError:
            return False
        return isinstance(data, dict)
