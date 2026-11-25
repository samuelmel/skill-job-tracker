"""`skilometer fetch-all`: roda todas as sources ativas, concorrencia global e 1 por plataforma."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from itertools import zip_longest

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from skilometer.core.exceptions import safe_error
from skilometer.database.models import FetchRunStatus, Platform, Source
from skilometer.ingestion.service import FetchRunSummary

logger = logging.getLogger("skilometer.fetch_all")

RunSource = Callable[[uuid.UUID], Awaitable[FetchRunSummary]]


class SourceTarget(BaseModel):
    source_id: uuid.UUID
    platform: str
    external_id: str


class SourceOutcome(BaseModel):
    target: SourceTarget
    status: FetchRunStatus
    jobs_found: int = 0
    raw_path: str | None = None
    raw_error: str | None = None
    error: str | None = None


class FetchAllReport(BaseModel):
    outcomes: list[SourceOutcome]

    def count(self, status: FetchRunStatus) -> int:
        return sum(1 for o in self.outcomes if o.status is status)

    @property
    def jobs_found(self) -> int:
        return sum(o.jobs_found for o in self.outcomes)

    @property
    def raw_failures(self) -> int:
        return sum(1 for o in self.outcomes if o.raw_error)

    def summary(self) -> dict[str, int]:
        """Contagens legiveis por maquina (`fetch-all --summary-json`)."""
        return {
            "attempted": len(self.outcomes),
            "succeeded": self.count(FetchRunStatus.SUCCEEDED),
            "partial": self.count(FetchRunStatus.PARTIAL),
            "failed": self.count(FetchRunStatus.FAILED),
            "jobs_found": self.jobs_found,
            "raw_failures": self.raw_failures,
        }

    @property
    def exit_code(self) -> int:
        """Nao-zero so quando toda source falhou (e havia ao menos uma)."""
        failed = self.count(FetchRunStatus.FAILED)
        return 1 if self.outcomes and failed == len(self.outcomes) else 0


async def list_active_targets(
    factory: async_sessionmaker[AsyncSession],
    *,
    platform: str | None = None,
    limit: int | None = None,
) -> list[SourceTarget]:
    """Sources habilitadas de plataformas com conector, intercaladas entre plataformas."""
    stmt = (
        select(Source.id, Platform.slug, Source.external_id)
        .join(Platform, Platform.id == Source.platform_id)
        .where(Source.enabled.is_(True), Platform.connector_type.is_not(None))
        .order_by(Platform.slug, Source.external_id)
    )
    if platform:
        stmt = stmt.where(Platform.slug == platform)
    async with factory() as session:
        rows = (await session.execute(stmt)).all()
    by_platform: dict[str, list[SourceTarget]] = {}
    for source_id, slug, external_id in rows:
        by_platform.setdefault(slug, []).append(
            SourceTarget(source_id=source_id, platform=slug, external_id=external_id)
        )
    targets = [t for group in zip_longest(*by_platform.values()) for t in group if t is not None]
    return targets[:limit] if limit is not None else targets


async def fetch_all(
    run_source: RunSource,
    targets: list[SourceTarget],
    *,
    concurrency: int,
) -> FetchAllReport:
    """Executa `run_source` por alvo. Uma falha nunca interrompe as demais."""
    global_sem = asyncio.Semaphore(max(1, concurrency))
    platform_sems = {t.platform: asyncio.Semaphore(1) for t in targets}

    async def one(target: SourceTarget) -> SourceOutcome:
        # Trava da plataforma antes da global: quem espera a plataforma nao ocupa vaga global.
        async with platform_sems[target.platform], global_sem:
            try:
                summary = await run_source(target.source_id)
            except Exception as exc:
                logger.error(
                    "fetch_all_source_failed",
                    extra={
                        "source_id": str(target.source_id),
                        "platform": target.platform,
                        "error": safe_error(exc),
                    },
                )
                return SourceOutcome(
                    target=target,
                    status=FetchRunStatus.FAILED,
                    error=safe_error(exc),
                )
        return SourceOutcome(
            target=target,
            status=summary.status,
            jobs_found=summary.jobs_found,
            raw_path=summary.raw_path,
            raw_error=summary.raw_error,
            error=summary.error_message,
        )

    return FetchAllReport(outcomes=list(await asyncio.gather(*(one(t) for t in targets))))
