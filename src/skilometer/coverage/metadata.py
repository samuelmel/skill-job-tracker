"""Metadados por plataforma, lidos de `platforms.json` (arquivo versionado, so stdlib json)."""

import enum
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter

PLATFORMS_JSON = Path(__file__).with_name("platforms.json")
SCHEMA_VERSION = 1


class ResearchStatus(enum.StrEnum):
    """Estado da pesquisa de uma plataforma SEM conector (valores de ImplementationStatus)."""

    NOT_STARTED = "NOT_STARTED"
    RESEARCHING = "RESEARCHING"
    BLOCKED = "BLOCKED"


class ExampleInstance(BaseModel):
    """Instancia publica real usada pelo `verify` (empresa + config do conector)."""

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)
    config: dict[str, Any]


class PlatformMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docs_urls: list[str] = Field(default_factory=list)
    extraction_strategy: str | None = None
    connector_type: str | None = None
    example_instances: list[ExampleInstance] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    research_status: ResearchStatus = ResearchStatus.NOT_STARTED


_URL = TypeAdapter(HttpUrl)


def parse_platforms(raw: dict[str, Any]) -> dict[str, PlatformMetadata]:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"platforms.json: schema_version deve ser {SCHEMA_VERSION}")
    platforms = raw.get("platforms")
    if not isinstance(platforms, dict):
        raise ValueError("platforms.json: chave 'platforms' ausente")
    parsed = {slug: PlatformMetadata.model_validate(item) for slug, item in platforms.items()}
    for slug, meta in parsed.items():
        for url in meta.docs_urls:
            _URL.validate_python(url)
        if meta.example_instances and not meta.connector_type:
            raise ValueError(f"{slug}: example_instances exige connector_type")
    return parsed


@lru_cache(maxsize=1)
def load_platform_metadata() -> dict[str, PlatformMetadata]:
    raw = json.loads(PLATFORMS_JSON.read_text(encoding="utf-8"))
    return parse_platforms(raw)
