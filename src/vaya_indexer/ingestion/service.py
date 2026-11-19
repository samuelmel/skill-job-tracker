"""Servico de ingestao: busca em uma source, normaliza e persiste de forma idempotente."""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vaya_indexer.connectors.base import BaseConnector, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import create_connector, get_connector_class
from vaya_indexer.core.exceptions import ConnectorNotFound, SourceNotFound
from vaya_indexer.database.models import (
    Company,
    FetchRunStatus,
    Job,
    JobSource,
    Source,
)
from vaya_indexer.database.repositories.companies import CompanyRepository
from vaya_indexer.database.repositories.fetch_runs import FetchRunRepository
from vaya_indexer.database.repositories.jobs import JobRepository
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.database.repositories.sources import SourceRepository
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.processing.normalization import (
    canonical_fingerprint,
    canonical_json,
    normalize_title,
    sha256_hex,
)

logger = logging.getLogger("vaya.ingestion")

# Campos do job canonico atualizados (e registrados em job_history) quando a fonte muda.
_TRACKED_FIELDS = (
    "title",
    "description",
    "location",
    "employment_type",
    "workplace_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "original_posted_at",
)
_CHUNK = 5000


class FetchRunSummary(BaseModel):
    run_id: uuid.UUID
    source_id: uuid.UUID
    status: FetchRunStatus
    jobs_found: int = 0
    jobs_inserted: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    jobs_closed: int = 0
    records_failed: int = 0
    error_message: str | None = None
    duration_ms: int = 0


@dataclass
class _Counts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0


def source_fingerprint(job: NormalizedJob) -> str:
    return sha256_hex(canonical_json(job.model_dump(mode="json")))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def register_source(
    session: AsyncSession,
    *,
    platform_slug: str,
    company_name: str | None,
    config: dict[str, Any],
    source_url: str | None = None,
) -> Source:
    """Cadastra (idempotente) uma source validando a config contra o conector da plataforma."""
    platform = await PlatformRepository(session).get_by_slug(platform_slug)
    if platform is None:
        raise ConnectorNotFound(
            f"Plataforma '{platform_slug}' nao encontrada (rode seed-platforms)"
        )
    if not platform.connector_type:
        raise ConnectorNotFound(f"Plataforma '{platform_slug}' ainda nao tem conector")
    connector_cls = get_connector_class(platform.connector_type)
    validated = connector_cls.validate_config(config)
    external_id = connector_cls.external_id_from_config(validated)
    sources = SourceRepository(session)
    existing = await sources.get_by_external_id(platform.id, external_id)
    if existing is not None:
        return existing
    company_id = (
        await CompanyRepository(session).get_or_create(company_name) if company_name else None
    )
    return await sources.create(
        platform_id=platform.id,
        company_id=company_id,
        external_id=external_id,
        source_url=source_url,
        configuration=validated.model_dump(),
    )


class IngestionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], http: HttpClient) -> None:
        self._session_factory = session_factory
        self._http = http

    async def run_source(self, source_id: uuid.UUID) -> FetchRunSummary:
        started = time.monotonic()
        async with self._session_factory() as session:
            source = await SourceRepository(session).get(source_id)
            if source is None:
                raise SourceNotFound(f"Source {source_id} nao encontrada")
            platform = await PlatformRepository(session).get(source.platform_id)
            assert platform is not None
            source_company = (
                await session.get(Company, source.company_id) if source.company_id else None
            )
            run = await FetchRunRepository(session).start(source_id)
            await session.commit()
            run_id = run.id
            connector_type = platform.connector_type
            config = dict(source.configuration)
            company_id = source.company_id
            company_name = source_company.name if source_company else None

        summary = FetchRunSummary(run_id=run_id, source_id=source_id, status=FetchRunStatus.RUNNING)
        try:
            if not connector_type:
                raise ConnectorNotFound(f"Plataforma '{platform.slug}' sem conector")
            connector = create_connector(connector_type, config, self._http)
            result = await connector.fetch_jobs()
            summary.jobs_found = len(result.jobs)
            async with self._session_factory() as session:
                counts = await self._persist(
                    session, connector, source_id, run_id, result.jobs, company_id, company_name
                )
                await session.commit()
            summary.jobs_inserted = counts.inserted
            summary.jobs_updated = counts.updated
            summary.jobs_unchanged = counts.unchanged
            summary.records_failed = counts.failed
            clean = counts.failed == 0 and result.complete
            summary.status = FetchRunStatus.SUCCEEDED if clean else FetchRunStatus.PARTIAL
            if not result.complete:
                summary.error_message = "listagem incompleta"
        except Exception as exc:
            logger.exception("fetch_failed", extra={"source_id": str(source_id)})
            summary.status = FetchRunStatus.FAILED
            summary.error_message = f"{type(exc).__name__}: {exc}"
        summary.duration_ms = round((time.monotonic() - started) * 1000)
        await self._finalize(summary)
        logger.info("fetch_finished", extra=summary.model_dump(mode="json"))
        return summary

    async def _finalize(self, summary: FetchRunSummary) -> None:
        async with self._session_factory() as session:
            run = await FetchRunRepository(session).get(summary.run_id)
            assert run is not None
            run.status = summary.status
            run.finished_at = datetime.now(UTC)
            run.jobs_found = summary.jobs_found
            run.jobs_inserted = summary.jobs_inserted
            run.jobs_updated = summary.jobs_updated
            run.jobs_unchanged = summary.jobs_unchanged
            run.jobs_closed = summary.jobs_closed
            run.records_failed = summary.records_failed
            run.error_message = summary.error_message
            run.duration_ms = summary.duration_ms
            if summary.status is FetchRunStatus.SUCCEEDED:
                source = await session.get(Source, summary.source_id)
                assert source is not None
                source.last_successful_fetch_at = run.finished_at
            await session.commit()

    async def _persist(
        self,
        session: AsyncSession,
        connector: BaseConnector,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        raw_jobs: list[RawJob],
        source_company_id: uuid.UUID | None,
        source_company_name: str | None,
    ) -> _Counts:
        jobs_repo = JobRepository(session)
        runs_repo = FetchRunRepository(session)
        known = await jobs_repo.job_sources_by_external_id(source_id)
        now = datetime.now(UTC)
        counts = _Counts()
        seen_unchanged: list[JobSource] = []

        for raw in raw_jobs:
            try:
                normalized = connector.normalize(raw)
                company_name = source_company_name or normalized.company_name
                if not company_name:
                    raise ValueError("empresa ausente: source sem company e vaga sem company_name")
                fingerprint = source_fingerprint(normalized)
                existing = known.get(raw.external_id)
                async with session.begin_nested():
                    if existing is None:
                        known[raw.external_id] = await self._insert(
                            session,
                            source_id,
                            raw,
                            normalized,
                            fingerprint,
                            source_company_id,
                            company_name,
                            now,
                        )
                        counts.inserted += 1
                    elif existing.source_fingerprint == fingerprint:
                        seen_unchanged.append(existing)
                        counts.unchanged += 1
                    else:
                        await self._update(
                            jobs_repo,
                            existing,
                            raw,
                            normalized,
                            fingerprint,
                            company_name,
                            source_id,
                            run_id,
                            now,
                        )
                        counts.updated += 1
            except Exception as exc:
                logger.warning(
                    "record_failed",
                    extra={"external_id": raw.external_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                runs_repo.add_error(
                    run_id,
                    external_job_id=raw.external_id,
                    error_type=type(exc).__name__,
                    message=str(exc)[:2000],
                    payload=raw.payload,
                )
                counts.failed += 1
        await self._touch_seen(session, seen_unchanged, now)
        return counts

    async def resolve_job_identity(
        self,
        session: AsyncSession,
        *,
        normalized: NormalizedJob,
        company_id: uuid.UUID,
        company_name: str,
        now: datetime,
    ) -> Job:
        """Ponto de extensao da Fase 2 (dedup cross-source).

        Hoje cada job_source novo gera um job canonico 1:1. A Fase 2 devera consultar
        `canonical_fingerprint`/similaridade e devolver um job existente quando houver match.
        """
        job = JobRepository(session).add_job(
            canonical_fingerprint=canonical_fingerprint(
                company_name, normalized.title, normalized.location
            ),
            company_id=company_id,
            normalized_title=normalize_title(normalized.title),
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            **normalized.model_dump(
                include=set(_TRACKED_FIELDS),
            ),
        )
        await session.flush()
        return job

    async def _insert(
        self,
        session: AsyncSession,
        source_id: uuid.UUID,
        raw: RawJob,
        normalized: NormalizedJob,
        fingerprint: str,
        source_company_id: uuid.UUID | None,
        company_name: str,
        now: datetime,
    ) -> JobSource:
        company_id = source_company_id or await CompanyRepository(session).get_or_create(
            company_name
        )
        job = await self.resolve_job_identity(
            session,
            normalized=normalized,
            company_id=company_id,
            company_name=company_name,
            now=now,
        )
        job_source = JobRepository(session).add_job_source(
            job_id=job.id,
            source_id=source_id,
            external_job_id=raw.external_id,
            original_url=normalized.original_url,
            application_url=normalized.application_url,
            raw_data=raw.payload,
            source_fingerprint=fingerprint,
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
        )
        await session.flush()
        return job_source

    async def _update(
        self,
        jobs_repo: JobRepository,
        job_source: JobSource,
        raw: RawJob,
        normalized: NormalizedJob,
        fingerprint: str,
        company_name: str,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        now: datetime,
    ) -> None:
        job = await jobs_repo.get_job(job_source.job_id)
        new_values = normalized.model_dump(include=set(_TRACKED_FIELDS))
        for field, new in new_values.items():
            old = getattr(job, field)
            if old != new:
                jobs_repo.add_history(
                    job_id=job.id,
                    source_id=source_id,
                    fetch_run_id=run_id,
                    field_name=field,
                    old_value=_jsonable(old),
                    new_value=_jsonable(new),
                    detected_at=now,
                )
                setattr(job, field, new)
        job.normalized_title = normalize_title(normalized.title)
        job.canonical_fingerprint = canonical_fingerprint(
            company_name, normalized.title, normalized.location
        )
        job.last_seen_at = job.last_checked_at = now
        job_source.original_url = normalized.original_url
        job_source.application_url = normalized.application_url
        job_source.raw_data = raw.payload
        job_source.source_fingerprint = fingerprint
        job_source.last_seen_at = job_source.last_checked_at = now
        job_source.missing_count = 0

    async def _touch_seen(
        self, session: AsyncSession, job_sources: list[JobSource], now: datetime
    ) -> None:
        """Atualiza last_seen/last_checked das vagas inalteradas em lote."""
        for i in range(0, len(job_sources), _CHUNK):
            chunk = job_sources[i : i + _CHUNK]
            await session.execute(
                update(JobSource)
                .where(JobSource.id.in_([js.id for js in chunk]))
                .values(last_seen_at=now, last_checked_at=now, missing_count=0)
            )
            await session.execute(
                update(Job)
                .where(Job.id.in_([js.job_id for js in chunk]))
                .values(last_seen_at=now, last_checked_at=now)
            )
