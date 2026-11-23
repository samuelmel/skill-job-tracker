"""Servico de ingestao: busca em uma source, normaliza e persiste de forma idempotente."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vaya_indexer.connectors.base import BaseConnector, FetchResult, NormalizedJob, RawJob
from vaya_indexer.connectors.registry import create_connector, get_connector_class
from vaya_indexer.core.config import ProcessingPolicy
from vaya_indexer.core.exceptions import ConnectorNotFound, SourceNotFound, safe_error
from vaya_indexer.database.models import (
    Company,
    DedupDecision,
    FetchRunStatus,
    Job,
    JobSource,
    JobStatus,
    Source,
)
from vaya_indexer.database.repositories.companies import CompanyRepository
from vaya_indexer.database.repositories.fetch_runs import FetchRunRepository
from vaya_indexer.database.repositories.jobs import JobRepository
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.database.repositories.sources import SourceRepository
from vaya_indexer.extraction.browser import BrowserFetcher
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.ingestion.job_status import refresh_job_status
from vaya_indexer.ingestion.reconciliation import ReconcileOutcome, apply_reconciliation
from vaya_indexer.lake.raw import RawWriter
from vaya_indexer.processing.dedup import (
    DedupAction,
    JobProfile,
    MatchScore,
    best_match,
    decide,
)
from vaya_indexer.processing.normalization import (
    canonical_fingerprint,
    canonical_json,
    normalize_title,
    sha256_hex,
)
from vaya_indexer.processing.urls import canonical_url

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
    "salary_period",
    "original_posted_at",
)
_CHUNK = 5000


def strip_nul(value: Any) -> Any:
    """Remove NUL (0x00) das strings de um JSON; o JSONB do PostgreSQL rejeita esse caractere."""
    if isinstance(value, str):
        return value.replace(chr(0), "")
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    return value


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
    raw_path: str | None = None
    raw_error: str | None = None


@dataclass
class JobResolution:
    """Resultado de `resolve_job_identity`: job canonico e a decisao de dedup que o originou."""

    job: Job
    action: DedupAction = DedupAction.NONE
    candidate_job_id: uuid.UUID | None = None
    match: MatchScore | None = None

    @property
    def merged(self) -> bool:
        return self.action is DedupAction.MERGE


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


CONCURRENT_MESSAGE = "execucao concorrente em andamento"


class ConcurrentRunError(Exception):
    """Outra run da mesma source esta persistindo/reconciliando."""


def source_lock_key(source_id: uuid.UUID) -> int:
    """Chave estavel (bigint com sinal) do advisory lock por source."""
    return int.from_bytes(source_id.bytes[:8], "big", signed=True)


async def _try_source_lock(session: AsyncSession, source_id: uuid.UUID) -> bool:
    """Lock transacional: vale ate o commit/rollback da persistencia + reconciliacao."""
    result = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": source_lock_key(source_id)}
    )
    return bool(result)


def _canon(url: str | None) -> str | None:
    return canonical_url(url) if url else None


def _canonical_urls(normalized: NormalizedJob) -> set[str]:
    return {u for u in (_canon(normalized.original_url), _canon(normalized.application_url)) if u}


async def _candidate_profiles(
    repo: JobRepository, candidates: list[Job]
) -> list[tuple[uuid.UUID, JobProfile]]:
    by_job: dict[uuid.UUID, list[JobSource]] = {}
    for js in await repo.job_sources_for_jobs([j.id for j in candidates]):
        by_job.setdefault(js.job_id, []).append(js)
    profiles: list[tuple[uuid.UUID, JobProfile]] = []
    for job in candidates:
        sources = by_job.get(job.id, [])
        urls = {u for js in sources for u in (js.canonical_url, js.canonical_application_url) if u}
        profile = JobProfile(
            company_id=job.company_id,
            title=job.title,
            location=job.location,
            description=job.description,
            urls=frozenset(urls),
            source_ids=frozenset(js.source_id for js in sources),
            fingerprint=job.canonical_fingerprint,
        )
        profiles.append((job.id, profile))
    return profiles


def _apply_fields(
    repo: JobRepository,
    job: Job,
    normalized: NormalizedJob,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    now: datetime,
    *,
    overwrite: bool,
) -> None:
    """Aplica os campos rastreados registrando job_history; sem `overwrite`, so preenche NULLs."""
    for field, new in normalized.model_dump(include=set(_TRACKED_FIELDS)).items():
        old = getattr(job, field)
        if old == new or not (overwrite or old is None):
            continue
        repo.add_history(
            job_id=job.id,
            source_id=source_id,
            fetch_run_id=run_id,
            field_name=field,
            old_value=_jsonable(old),
            new_value=_jsonable(new),
            detected_at=now,
        )
        setattr(job, field, new)


class IngestionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        http: HttpClient,
        policy: ProcessingPolicy | None = None,
        browser: BrowserFetcher | None = None,
        raw_writer: RawWriter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._http = http
        self._browser = browser
        self._raw_writer = raw_writer
        self._policy = policy or ProcessingPolicy()

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
            platform_slug = platform.slug
            source_external_id = source.external_id
            config = dict(source.configuration)
            company_id = source.company_id
            company_name = source_company.name if source_company else None

        summary = FetchRunSummary(run_id=run_id, source_id=source_id, status=FetchRunStatus.RUNNING)
        try:
            if not connector_type:
                raise ConnectorNotFound(f"Plataforma '{platform.slug}' sem conector")
            connector = create_connector(connector_type, config, self._http, self._browser)
            result = await connector.fetch_jobs()
            fetched_at = datetime.now(UTC)
            summary.jobs_found = len(result.jobs)
            await self._write_raw(
                summary,
                connector,
                platform_slug=platform_slug,
                source_external_id=source_external_id,
                fetched_at=fetched_at,
                result=result,
            )
            async with self._session_factory() as session:
                if not await _try_source_lock(session, source_id):
                    raise ConcurrentRunError(CONCURRENT_MESSAGE)
                counts = await self._persist(
                    session, connector, source_id, run_id, result.jobs, company_id, company_name
                )
                clean = counts.failed == 0 and result.complete
                outcome = ReconcileOutcome()
                if clean:
                    outcome = await apply_reconciliation(
                        session,
                        source_id=source_id,
                        run_id=run_id,
                        seen_external_ids={r.external_id for r in result.jobs},
                        jobs_found=len(result.jobs),
                        complete=result.complete,
                        records_failed=counts.failed,
                        now=datetime.now(UTC),
                        policy=self._policy,
                    )
                await session.commit()
            summary.jobs_inserted = counts.inserted
            summary.jobs_updated = counts.updated
            summary.jobs_unchanged = counts.unchanged
            summary.records_failed = counts.failed
            summary.jobs_closed = outcome.jobs_closed
            ok = clean and not outcome.blocked
            summary.status = FetchRunStatus.SUCCEEDED if ok else FetchRunStatus.PARTIAL
            if not result.complete:
                summary.error_message = "listagem incompleta"
            elif outcome.message:
                summary.error_message = outcome.message
        except ConcurrentRunError as exc:
            summary.status = FetchRunStatus.FAILED
            summary.error_message = str(exc)
        except Exception as exc:
            # Sem traceback nem mensagem: o log e publico e a mensagem pode ter conteudo de vaga.
            logger.error(
                "fetch_failed",
                extra={"source_id": str(source_id), "error": safe_error(exc)},
            )
            summary.status = FetchRunStatus.FAILED
            summary.error_message = safe_error(exc)
        summary.duration_ms = round((time.monotonic() - started) * 1000)
        await self._finalize(summary)
        logger.info("fetch_finished", extra=summary.model_dump(mode="json"))
        return summary

    async def _write_raw(
        self,
        summary: FetchRunSummary,
        connector: BaseConnector,
        *,
        platform_slug: str,
        source_external_id: str,
        fetched_at: datetime,
        result: FetchResult,
    ) -> None:
        """Grava o raw antes da persistencia; falha aqui e registrada mas nunca bloqueia."""
        if self._raw_writer is None:
            return
        try:
            written = await asyncio.to_thread(
                self._raw_writer.write,
                platform=platform_slug,
                source_external_id=source_external_id,
                source_id=summary.source_id,
                run_id=summary.run_id,
                fetched_at=fetched_at,
                complete=result.complete,
                jobs=result.jobs,
                connector=type(connector).__name__,
            )
            summary.raw_path = written.path
        except Exception as exc:
            summary.raw_error = safe_error(exc)
            logger.error(
                "raw_write_failed",
                extra={
                    "source_id": str(summary.source_id),
                    "run_id": str(summary.run_id),
                    "platform": platform_slug,
                    "source_external_id": source_external_id,
                    "jobs_found": summary.jobs_found,
                    "error": summary.raw_error,
                },
            )

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
                            run_id,
                            raw,
                            normalized,
                            fingerprint,
                            source_company_id,
                            company_name,
                            now,
                        )
                        counts.inserted += 1
                        continue
                    reopened = existing.status is not JobStatus.OPEN
                    if reopened:
                        await self._reopen(session, jobs_repo, existing, run_id, now)
                    if existing.source_fingerprint != fingerprint:
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
                    elif reopened:
                        await self._touch_one(session, existing, now)
                        counts.updated += 1
                    else:
                        seen_unchanged.append(existing)
                        counts.unchanged += 1
            except Exception as exc:
                logger.warning(
                    "record_failed",
                    extra={"external_id": raw.external_id, "error": safe_error(exc)},
                )
                runs_repo.add_error(
                    run_id,
                    external_job_id=raw.external_id,
                    error_type=type(exc).__name__,
                    message=str(exc)[:2000],
                    payload=strip_nul(raw.payload),
                )
                counts.failed += 1
        await self._touch_seen(session, seen_unchanged, now)
        return counts

    async def resolve_job_identity(
        self,
        session: AsyncSession,
        *,
        source_id: uuid.UUID,
        normalized: NormalizedJob,
        company_id: uuid.UUID,
        company_name: str,
        now: datetime,
    ) -> JobResolution:
        """Dedup cross-source: devolve um job existente (MERGE) ou cria um novo (NONE/REVIEW).

        Candidatos: so jobs da mesma empresa, filtrados por fingerprint/URL/titulo e com LIMIT.
        """
        repo = JobRepository(session)
        fingerprint = canonical_fingerprint(company_name, normalized.title, normalized.location)
        normalized_title = normalize_title(normalized.title)
        best: tuple[uuid.UUID, MatchScore] | None = None
        if normalized_title:
            candidates = await repo.find_dedup_candidates(
                company_id=company_id,
                fingerprint=fingerprint,
                normalized_title=normalized_title,
                canonical_urls=sorted(_canonical_urls(normalized)),
                limit=self._policy.dedup_candidate_limit,
            )
            new_profile = JobProfile(
                company_id=company_id,
                title=normalized.title,
                location=normalized.location,
                description=normalized.description,
                urls=frozenset(_canonical_urls(normalized)),
                source_ids=frozenset({source_id}),
                fingerprint=fingerprint,
            )
            best = best_match(
                new_profile,
                await _candidate_profiles(repo, candidates),
                self._policy.dedup_url_min_title_similarity,
            )
        action = DedupAction.NONE
        if best is not None:
            action = decide(
                best[1], self._policy.dedup_merge_threshold, self._policy.dedup_review_threshold
            )
        if action is DedupAction.MERGE and best is not None:
            return JobResolution(await repo.get_job(best[0]), action, best[0], best[1])
        job = repo.add_job(
            canonical_fingerprint=fingerprint,
            company_id=company_id,
            normalized_title=normalized_title,
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
            **normalized.model_dump(include=set(_TRACKED_FIELDS)),
        )
        await session.flush()
        if action is DedupAction.REVIEW and best is not None:
            return JobResolution(job, action, best[0], best[1])
        return JobResolution(job)

    async def _insert(
        self,
        session: AsyncSession,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
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
        resolution = await self.resolve_job_identity(
            session,
            source_id=source_id,
            normalized=normalized,
            company_id=company_id,
            company_name=company_name,
            now=now,
        )
        repo = JobRepository(session)
        job_source = repo.add_job_source(
            job_id=resolution.job.id,
            source_id=source_id,
            external_job_id=raw.external_id,
            original_url=normalized.original_url,
            application_url=normalized.application_url,
            canonical_url=_canon(normalized.original_url),
            canonical_application_url=_canon(normalized.application_url),
            raw_data=strip_nul(raw.payload),
            source_fingerprint=fingerprint,
            first_seen_at=now,
            last_seen_at=now,
            last_checked_at=now,
        )
        await session.flush()
        match = resolution.match
        if match is not None and resolution.candidate_job_id is not None:
            if resolution.merged:
                repo.add_dedup_match(
                    job_id_a=resolution.job.id,
                    job_id_b=None,
                    job_source_id=job_source.id,
                    score=match.score,
                    method=match.method,
                    reasons=match.reasons,
                    decision=DedupDecision.MERGED,
                )
                # Job canonico com varias fontes: mantem os campos do primeiro, so preenche NULLs.
                _apply_fields(
                    repo, resolution.job, normalized, source_id, run_id, now, overwrite=False
                )
                resolution.job.last_seen_at = resolution.job.last_checked_at = now
                await session.flush()
                await refresh_job_status(
                    session, resolution.job.id, source_id=source_id, run_id=run_id, now=now
                )
            else:
                repo.add_dedup_match(
                    job_id_a=resolution.candidate_job_id,
                    job_id_b=resolution.job.id,
                    job_source_id=job_source.id,
                    score=match.score,
                    method=match.method,
                    reasons=match.reasons,
                    decision=DedupDecision.REVIEW,
                )
        await session.flush()
        return job_source

    async def _reopen(
        self,
        session: AsyncSession,
        repo: JobRepository,
        job_source: JobSource,
        run_id: uuid.UUID,
        now: datetime,
    ) -> None:
        """Vaga que reaparece volta a OPEN (job_source e, se preciso, o job) com historico."""
        repo.add_history(
            job_id=job_source.job_id,
            source_id=job_source.source_id,
            fetch_run_id=run_id,
            field_name="source_status",
            old_value=job_source.status.value,
            new_value=JobStatus.OPEN.value,
            detected_at=now,
        )
        job_source.status = JobStatus.OPEN
        job_source.missing_count = 0
        await session.flush()
        await refresh_job_status(
            session, job_source.job_id, source_id=job_source.source_id, run_id=run_id, now=now
        )

    @staticmethod
    async def _touch_one(session: AsyncSession, job_source: JobSource, now: datetime) -> None:
        job_source.last_seen_at = job_source.last_checked_at = now
        job_source.missing_count = 0
        job = await session.get(Job, job_source.job_id)
        assert job is not None
        job.last_seen_at = job.last_checked_at = now

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
        # So a fonte primaria sobrescreve campos; as demais apenas preenchem NULLs.
        primary = await jobs_repo.primary_job_source_id(job.id) == job_source.id
        _apply_fields(jobs_repo, job, normalized, source_id, run_id, now, overwrite=primary)
        if primary:
            job.normalized_title = normalize_title(normalized.title)
            job.canonical_fingerprint = canonical_fingerprint(
                company_name, normalized.title, normalized.location
            )
        job.last_seen_at = job.last_checked_at = now
        job_source.original_url = normalized.original_url
        job_source.application_url = normalized.application_url
        job_source.canonical_url = _canon(normalized.original_url)
        job_source.canonical_application_url = _canon(normalized.application_url)
        job_source.raw_data = strip_nul(raw.payload)
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
