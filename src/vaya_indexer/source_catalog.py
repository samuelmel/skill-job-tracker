"""Catalogo versionado de fontes (catalog/sources.json) e carga idempotente no banco.

Cada entrada tem so identificadores publicos de board; nunca segredos.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vaya_indexer.database.models import Source
from vaya_indexer.ingestion.service import register_source

CATALOG_VERSION = 1
DEFAULT_CATALOG_PATH = Path("catalog/sources.json")


class CatalogEntry(BaseModel):
    platform: str = Field(min_length=1)
    company: str | None = None
    config: dict[str, Any]
    source_url: str | None = None


class SourceCatalog(BaseModel):
    version: int = CATALOG_VERSION
    sources: list[CatalogEntry]


@dataclass
class LoadResult:
    total: int = 0
    created: int = 0
    existing: int = 0
    errors: list[str] = field(default_factory=list)


def read_catalog(path: Path) -> SourceCatalog:
    return SourceCatalog.model_validate(json.loads(path.read_text(encoding="utf-8")))


async def _count_sources(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Source)) or 0


async def load_catalog(session: AsyncSession, catalog: SourceCatalog) -> LoadResult:
    """Cadastra as entradas ausentes; reexecutar nao duplica nada. Erros por entrada nao param."""
    result = LoadResult(total=len(catalog.sources))
    before = await _count_sources(session)
    for entry in catalog.sources:
        try:
            async with session.begin_nested():
                await register_source(
                    session,
                    platform_slug=entry.platform,
                    company_name=entry.company,
                    config=entry.config,
                    source_url=entry.source_url,
                )
        except Exception as exc:
            result.errors.append(f"{entry.platform} {entry.config}: {type(exc).__name__}: {exc}")
    await session.commit()
    result.created = await _count_sources(session) - before
    result.existing = result.total - result.created - len(result.errors)
    return result
