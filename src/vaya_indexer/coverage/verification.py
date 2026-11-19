"""Verificacao real de plataformas: roda o conector contra instancias publicas e grava evidencia.

Regra de status (reflete a ULTIMA verificacao; as linhas antigas de `platform_verifications`
nunca sao apagadas, entao o historico de evidencia permanece):

* alguma instancia com run SUCCEEDED, `jobs_found >= 1` e nao todos os registros falhos
  -> VERIFIED (atualiza `last_verified_at` / `last_verified_job_count`);
* so falhas transitorias (rede/retries esgotados, execucao concorrente, browser ausente)
  -> status inalterado: uma falha de rede nao apaga nem rebaixa evidencia anterior;
* falha nao transitoria (board vazio, 404, parse, config invalida, normalizacao falha)
  -> TESTED se a fixture sintetica do conector normaliza (teste mockado ok), senao IMPLEMENTED.
  Evidencia antiga nao sustenta VERIFIED contra uma verificacao nova que falhou de verdade.

Plataformas sem conector nao sao tocadas (mantem o status da pesquisa).
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vaya_indexer import __version__
from vaya_indexer.connectors.base import BaseConnector, RawJob
from vaya_indexer.connectors.registry import get_connector_class
from vaya_indexer.core.exceptions import ConnectorNotFound, VayaError
from vaya_indexer.coverage.metadata import ExampleInstance, PlatformMetadata, load_platform_metadata
from vaya_indexer.database.models import (
    FetchRunStatus,
    ImplementationStatus,
    Job,
    JobSource,
    JobStatus,
    Platform,
    PlatformVerification,
)
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.ingestion.service import (
    CONCURRENT_MESSAGE,
    FetchRunSummary,
    IngestionService,
    register_source,
)

logger = logging.getLogger("vaya.coverage")

SAMPLE_SIZE = 5
_TRANSIENT_PREFIXES = ("RetryExhausted", "BrowserUnavailable", CONCURRENT_MESSAGE)
_VERIFIABLE = (
    ImplementationStatus.IMPLEMENTED,
    ImplementationStatus.TESTED,
    ImplementationStatus.VERIFIED,
)


@dataclass
class InstanceResult:
    company: str
    source_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    jobs_found: int = 0
    records_failed: int = 0
    success: bool = False
    transient: bool = False
    error: str | None = None
    sample_titles: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)


@dataclass
class PlatformResult:
    slug: str
    status_before: ImplementationStatus
    status_after: ImplementationStatus
    instances: list[InstanceResult] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def verified(self) -> bool:
        return any(i.success for i in self.instances)


def is_transient(error: str | None) -> bool:
    return bool(error) and str(error).startswith(_TRANSIENT_PREFIXES)


def fixture_normalizes(connector_cls: type[BaseConnector], config: dict[str, Any]) -> bool:
    """Teste mockado minimo: a fixture sintetica do conector normaliza sem erro."""
    path = connector_cls.fixture_path
    if path is None or not path.exists():
        return False
    try:
        raw_jobs = [
            RawJob.model_validate(r) for r in json.loads(path.read_text("utf-8"))["raw_jobs"]
        ]
        # normalize nao usa rede: instancia sem __init__ (sem HttpClient), so com a config.
        connector = connector_cls.__new__(connector_cls)
        connector.config = connector_cls.validate_config(config)
        return bool(raw_jobs) and all(connector.normalize(r).title for r in raw_jobs)
    except Exception:
        return False


def decide_status(
    current: ImplementationStatus, results: list[InstanceResult], fixture_ok: bool
) -> ImplementationStatus:
    if any(r.success for r in results):
        return ImplementationStatus.VERIFIED
    if all(r.transient for r in results):
        return current
    return ImplementationStatus.TESTED if fixture_ok else ImplementationStatus.IMPLEMENTED


def _success(summary: FetchRunSummary) -> bool:
    return (
        summary.status is FetchRunStatus.SUCCEEDED
        and summary.jobs_found >= 1
        and summary.records_failed < summary.jobs_found
    )


def _failure_reason(summary: FetchRunSummary) -> str | None:
    if summary.error_message:
        return summary.error_message
    if summary.jobs_found == 0:
        return "fonte respondeu sem vagas"
    if summary.records_failed >= summary.jobs_found:
        return "normalizacao falhou para todos os registros"
    return f"run terminou com status {summary.status.value}"


class VerificationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ingestion: IngestionService,
        metadata: dict[str, PlatformMetadata] | None = None,
    ) -> None:
        self._factory = session_factory
        self._ingestion = ingestion
        self._metadata = metadata if metadata is not None else load_platform_metadata()

    async def verify_platform(self, slug: str, *, concurrency: int = 3) -> PlatformResult:
        async with self._factory() as session:
            platform = await PlatformRepository(session).get_by_slug(slug)
            if platform is None:
                raise ConnectorNotFound(f"Plataforma '{slug}' nao encontrada (rode seed-platforms)")
            platform_id, connector_type = platform.id, platform.connector_type
            current = platform.implementation_status
        meta = self._metadata.get(slug)
        result = PlatformResult(slug, current, current)
        if not connector_type:
            result.skipped_reason = "sem conector"
            return result
        if meta is None or not meta.example_instances:
            result.skipped_reason = "sem example_instances"
            return result
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(instance: ExampleInstance) -> InstanceResult:
            async with sem:
                return await self._verify_instance(slug, instance)

        result.instances = list(await asyncio.gather(*(one(i) for i in meta.example_instances)))
        fixture_ok = fixture_normalizes(
            get_connector_class(connector_type), meta.example_instances[0].config
        )
        new_status = decide_status(current, result.instances, fixture_ok)
        await self._persist(platform_id, result, new_status)
        result.status_after = new_status
        return result

    async def verify_all(self, *, concurrency: int = 3) -> list[PlatformResult]:
        async with self._factory() as session:
            rows = (
                await session.scalars(
                    select(Platform)
                    .where(Platform.implementation_status.in_(_VERIFIABLE))
                    .order_by(Platform.slug)
                )
            ).all()
            slugs = [p.slug for p in rows]
        return [await self.verify_platform(s, concurrency=concurrency) for s in slugs]

    async def _verify_instance(self, slug: str, instance: ExampleInstance) -> InstanceResult:
        out = InstanceResult(company=instance.company)
        try:
            async with self._factory() as session:
                source = await register_source(
                    session,
                    platform_slug=slug,
                    company_name=instance.company,
                    config=instance.config,
                )
                await session.commit()
                out.source_id = source.id
            summary = await self._ingestion.run_source(out.source_id)
        except Exception as exc:  # falha isolada por instancia; vira evidencia negativa
            logger.exception("verify_instance_failed", extra={"platform": slug})
            out.error = f"{type(exc).__name__}: {exc}"
            out.transient = isinstance(exc, VayaError) and is_transient(out.error)
            return out
        out.run_id = summary.run_id
        out.jobs_found = summary.jobs_found
        out.records_failed = summary.records_failed
        out.success = _success(summary)
        if not out.success:
            out.error = _failure_reason(summary)
            out.transient = is_transient(out.error)
        if summary.jobs_found >= 1:
            out.sample_titles, out.sample_urls = await self._samples(out.source_id)
        return out

    async def _samples(self, source_id: uuid.UUID) -> tuple[list[str], list[str]]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(Job.title, JobSource.original_url)
                    .join(JobSource, JobSource.job_id == Job.id)
                    .where(JobSource.source_id == source_id, JobSource.status == JobStatus.OPEN)
                    .order_by(Job.title)
                    .limit(SAMPLE_SIZE)
                )
            ).all()
        return [r[0] for r in rows], [r[1] for r in rows if r[1]]

    async def _persist(
        self, platform_id: uuid.UUID, result: PlatformResult, new_status: ImplementationStatus
    ) -> None:
        now = datetime.now(UTC)
        async with self._factory() as session:
            for inst in result.instances:
                session.add(
                    PlatformVerification(
                        platform_id=platform_id,
                        source_id=inst.source_id,
                        fetch_run_id=inst.run_id,
                        verified_at=now,
                        jobs_extracted=inst.jobs_found,
                        sample_job_titles=inst.sample_titles[:SAMPLE_SIZE],
                        sample_urls=inst.sample_urls[:SAMPLE_SIZE],
                        success=inst.success,
                        error_message=inst.error,
                        connector_version=__version__,
                    )
                )
            platform = await session.get(Platform, platform_id)
            assert platform is not None
            platform.implementation_status = new_status
            if result.verified:
                platform.last_verified_at = now
                platform.last_verified_job_count = sum(
                    i.jobs_found for i in result.instances if i.success
                )
            await session.commit()
