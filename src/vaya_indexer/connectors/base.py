"""Contrato dos conectores e modelos de dados trocados com a ingestao."""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, Field, ValidationError

from vaya_indexer.core.exceptions import ConnectorConfigError
from vaya_indexer.extraction.http import HttpClient


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
    employment_type: str | None = None
    workplace_type: str | None = None
    original_posted_at: datetime | None = None


class FetchResult(BaseModel):
    jobs: list[RawJob]
    complete: bool = Field(description="True se a listagem foi obtida de forma integral")


class BaseConnector(ABC):
    platform_name: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]

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
