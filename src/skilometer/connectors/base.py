"""Contrato dos conectores e modelos de dados trocados com a ingestao."""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError, field_validator

from skilometer.core.exceptions import ConnectorConfigError
from skilometer.extraction.browser import BrowserFetcher
from skilometer.extraction.http import HttpClient


class RawJob(BaseModel):
    """Vaga como veio da fonte; `payload` e o dado original, intacto."""

    external_id: str = Field(min_length=1)
    payload: dict[str, Any]


class NormalizedJob(BaseModel):
    """Vaga normalizada. Campos que a fonte nao informa ficam None (nunca inventados)."""

    title: str = Field(min_length=1)
    company_name: str | None = None
    description: str | None = None
    location: str | None = None
    original_url: str | None = None
    application_url: str | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    salary_period: str | None = None
    employment_type: str | None = None
    workplace_type: str | None = None
    original_posted_at: datetime | None = None

    @field_validator("*", mode="after")
    @classmethod
    def _strip_nul(cls, value: Any) -> Any:
        """PostgreSQL rejeita NUL (0x00) em texto: remove, sem perder a vaga."""
        return value.replace("\x00", "") if isinstance(value, str) else value

    @field_validator("employment_type", mode="after")
    @classmethod
    def _cap_employment_type(cls, value: str | None) -> str | None:
        """A coluna e String(60); valor maior e truncado em vez de derrubar a vaga."""
        return value[:60] if value is not None else None


class FetchResult(BaseModel):
    jobs: list[RawJob]
    complete: bool = Field(description="True se a listagem foi obtida de forma integral")


class BaseConnector(ABC):
    platform_name: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    # True so para conectores que dependem de JS: a ingestao injeta `self.browser`.
    requires_browser: ClassVar[bool] = False
    # JSON sintetico {"synthetic": true, "raw_jobs": [{external_id, payload}]} p/ teste de contrato.
    fixture_path: ClassVar[Path | None] = None
    browser: BrowserFetcher | None = None

    def __init__(self, http: HttpClient, **config: Any) -> None:
        self.http = http
        self.config = self.validate_config(config)

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> BaseModel:
        try:
            return cls.config_model(**config)
        except ValidationError as exc:
            raise ConnectorConfigError(
                f"Configuracao invalida para '{cls.platform_name}': {exc}"
            ) from exc

    @classmethod
    @abstractmethod
    def external_id_from_config(cls, config: BaseModel) -> str:
        """Identificador estavel da fonte (ex.: board token)."""

    @abstractmethod
    async def fetch_jobs(self) -> FetchResult: ...

    @abstractmethod
    def normalize(self, raw: RawJob) -> NormalizedJob: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
