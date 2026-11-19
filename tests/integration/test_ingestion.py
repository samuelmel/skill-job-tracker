import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload
from vaya_indexer.database.models import (
    ConnectorError,
    FetchRun,
    FetchRunStatus,
    Job,
    JobHistory,
    JobSource,
    JobStatus,
    Platform,
    Source,
)
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.ingestion.service import IngestionService, register_source
from vaya_indexer.platforms_catalog import build_catalog

pytestmark = pytest.mark.usefixtures("seeded")

Factory = async_sessionmaker[AsyncSession]


async def _noop_sleep(_: float) -> None:
    return None


@pytest.fixture
async def http() -> AsyncIterator[HttpClient]:
    async with HttpClient(user_agent="vaya-test", max_retries=1, sleep=_noop_sleep) as client:
        yield client


@pytest.fixture
async def source_id(session_factory: Factory) -> uuid.UUID:
    async with session_factory() as session:
        source = await register_source(
            session,
            platform_slug="greenhouse",
            company_name="Acme Inc.",
            config={"board_token": BOARD_TOKEN},
        )
        await session.commit()
        return source.id


async def _count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_migration_creates_schema_and_search_vector(session_factory: Factory) -> None:
    async with session_factory() as session:
        tables = set(
            await session.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
        assert {
            "platforms", "companies", "company_aliases", "sources", "jobs", "job_sources",
            "fetch_runs", "job_history", "dedup_matches", "connector_errors", "alembic_version",
        } <= tables  # fmt: skip
        indexes = set(await session.scalars(text("SELECT indexname FROM pg_indexes")))
        assert "ix_jobs_search_vector" in indexes
        assert "ix_fetch_runs_source_id_started_at" in indexes


async def test_seed_platforms_is_idempotent(session_factory: Factory) -> None:
    async with session_factory() as session:
        await PlatformRepository(session).upsert_catalog(build_catalog())
        await session.commit()
        assert await _count(session, Platform) == 78
        greenhouse = await PlatformRepository(session).get_by_slug("greenhouse")
        assert greenhouse is not None and greenhouse.connector_type == "greenhouse"
        assert greenhouse.implementation_status.value == "IMPLEMENTED"


@respx.mock
async def test_ingestion_persists_everything(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    summary = await IngestionService(session_factory, http).run_source(source_id)

    assert summary.status is FetchRunStatus.SUCCEEDED
    assert (summary.jobs_found, summary.jobs_inserted) == (3, 3)
    assert summary.jobs_updated == summary.jobs_unchanged == summary.records_failed == 0
    async with session_factory() as session:
        assert await _count(session, Job) == 3
        assert await _count(session, JobSource) == 3
        run = await session.get(FetchRun, summary.run_id)
        assert run is not None and run.status is FetchRunStatus.SUCCEEDED
        assert run.finished_at is not None and run.duration_ms is not None
        source = await session.get(Source, source_id)
        assert source is not None and source.last_successful_fetch_at is not None
        job = await session.scalar(select(Job).where(Job.title == "Senior Backend Engineer"))
        assert job is not None
        assert job.normalized_title == "senior backend engineer"
        assert job.description == "<p>Build &amp; run services.</p>"
        assert job.status is JobStatus.OPEN
        assert job.salary_min is None
        js = await session.scalar(select(JobSource).where(JobSource.job_id == job.id))
        assert js is not None and js.raw_data["id"] == 1001  # payload original intacto
        match = await session.scalar(
            select(func.count()).where(
                Job.search_vector.op("@@")(func.plainto_tsquery("simple", "backend"))
            )
        )
        assert match == 1


@respx.mock
async def test_second_run_is_idempotent(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    service = IngestionService(session_factory, http)
    await service.run_source(source_id)
    second = await service.run_source(source_id)

    assert (second.jobs_inserted, second.jobs_updated, second.jobs_unchanged) == (0, 0, 3)
    async with session_factory() as session:
        assert await _count(session, Job) == 3
        assert await _count(session, JobSource) == 3
        assert await _count(session, FetchRun) == 2


@respx.mock
async def test_changed_payload_counts_as_updated_and_records_history(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    service = IngestionService(session_factory, http)
    await service.run_source(source_id)

    changed = board_payload()
    changed["jobs"][1]["title"] = "Senior Data Analyst II"
    route.mock(return_value=httpx.Response(200, json=changed))
    summary = await service.run_source(source_id)

    assert (summary.jobs_inserted, summary.jobs_updated, summary.jobs_unchanged) == (0, 1, 2)
    async with session_factory() as session:
        assert await _count(session, Job) == 3
        job = await session.scalar(select(Job).where(Job.title == "Senior Data Analyst II"))
        assert job is not None and job.normalized_title == "senior data analyst ii"
        history = (await session.scalars(select(JobHistory))).all()
        assert len(history) == 1
        assert history[0].field_name == "title"
        assert history[0].old_value == "Data Analyst II"
        assert history[0].new_value == "Senior Data Analyst II"


@respx.mock
async def test_total_http_failure_marks_run_failed_and_keeps_jobs_open(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    service = IngestionService(session_factory, http)
    await service.run_source(source_id)

    route.mock(return_value=httpx.Response(503))
    failed = await service.run_source(source_id)

    assert failed.status is FetchRunStatus.FAILED
    assert failed.error_message is not None and "RetryExhausted" in failed.error_message
    async with session_factory() as session:
        statuses = set(await session.scalars(select(Job.status)))
        assert statuses == {JobStatus.OPEN}
        assert set(await session.scalars(select(JobSource.status))) == {JobStatus.OPEN}
        source = await session.get(Source, source_id)
        assert source is not None and source.last_successful_fetch_at is not None  # da run anterior
        run = await session.get(FetchRun, failed.run_id)
        assert run is not None and run.status is FetchRunStatus.FAILED


@respx.mock
async def test_invalid_record_is_isolated(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    payload = board_payload()
    payload["jobs"][1]["title"] = ""  # registro invalido
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=payload))
    summary = await IngestionService(session_factory, http).run_source(source_id)

    assert summary.status is FetchRunStatus.PARTIAL
    assert (summary.jobs_found, summary.jobs_inserted, summary.records_failed) == (3, 2, 1)
    async with session_factory() as session:
        assert await _count(session, Job) == 2
        errors = (await session.scalars(select(ConnectorError))).all()
        assert len(errors) == 1
        assert errors[0].external_job_id == "1002"
        assert errors[0].error_type == "ValidationError"
        assert errors[0].payload is not None and errors[0].payload["id"] == 1002
