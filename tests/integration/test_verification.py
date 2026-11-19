"""verify + coverage-report contra Postgres real, com a API do Greenhouse mockada (respx)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload
from vaya_indexer.coverage.metadata import ExampleInstance, PlatformMetadata
from vaya_indexer.coverage.report import build_report, render_markdown, write_report
from vaya_indexer.coverage.verification import VerificationService
from vaya_indexer.database.models import ImplementationStatus, Platform, PlatformVerification
from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.ingestion.service import IngestionService
from vaya_indexer.platforms_catalog import build_catalog

pytestmark = pytest.mark.usefixtures("seeded")

Factory = async_sessionmaker[AsyncSession]

METADATA = {
    "greenhouse": PlatformMetadata(
        connector_type="greenhouse",
        example_instances=[
            ExampleInstance(company="Acme Inc.", config={"board_token": BOARD_TOKEN})
        ],
    ),
    "adp": PlatformMetadata(),
}


async def _noop_sleep(_: float) -> None:
    return None


@pytest.fixture
async def service(session_factory: Factory) -> AsyncIterator[VerificationService]:
    async with HttpClient(user_agent="vaya-test", max_retries=1, sleep=_noop_sleep) as http:
        yield VerificationService(
            session_factory, IngestionService(session_factory, http), METADATA
        )


async def _platform(factory: Factory, slug: str) -> Platform:
    async with factory() as session:
        platform = await PlatformRepository(session).get_by_slug(slug)
        assert platform is not None
        return platform


async def _evidence(factory: Factory) -> list[PlatformVerification]:
    async with factory() as session:
        return list(
            (
                await session.scalars(
                    select(PlatformVerification).order_by(PlatformVerification.verified_at)
                )
            ).all()
        )


@respx.mock
async def test_verify_success_records_evidence_and_verifies(
    service: VerificationService, session_factory: Factory
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    result = await service.verify_platform("greenhouse")
    assert result.verified and result.status_after is ImplementationStatus.VERIFIED
    (row,) = await _evidence(session_factory)
    assert row.success and row.jobs_extracted == 3 and row.fetch_run_id and row.source_id
    assert row.sample_job_titles == [
        "Data Analyst II",
        "Product Designer",
        "Senior Backend Engineer",
    ]
    assert len(row.sample_urls) == 3 and row.connector_version
    platform = await _platform(session_factory, "greenhouse")
    assert platform.implementation_status is ImplementationStatus.VERIFIED
    assert platform.last_verified_at is not None and platform.last_verified_job_count == 3


@respx.mock
async def test_verify_is_idempotent_on_sources(
    service: VerificationService, session_factory: Factory
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    await service.verify_platform("greenhouse")
    await service.verify_platform("greenhouse")
    rows = await _evidence(session_factory)
    assert len(rows) == 2 and len({r.source_id for r in rows}) == 1


@respx.mock
async def test_network_failure_keeps_verified_and_prior_evidence(
    service: VerificationService, session_factory: Factory
) -> None:
    route = respx.get(JOBS_URL)
    route.mock(return_value=httpx.Response(200, json=board_payload()))
    await service.verify_platform("greenhouse")
    before = await _platform(session_factory, "greenhouse")

    route.mock(return_value=httpx.Response(503))
    result = await service.verify_platform("greenhouse")
    assert not result.verified and result.instances[0].transient
    assert result.status_after is ImplementationStatus.VERIFIED

    after = await _platform(session_factory, "greenhouse")
    assert after.implementation_status is ImplementationStatus.VERIFIED
    assert after.last_verified_at == before.last_verified_at
    assert after.last_verified_job_count == 3
    ok, failed = await _evidence(session_factory)
    assert ok.success and ok.sample_job_titles  # evidencia antiga intacta
    assert not failed.success and failed.error_message and "RetryExhausted" in failed.error_message
    assert failed.jobs_extracted == 0 and failed.sample_job_titles == []


@respx.mock
async def test_empty_board_downgrades_to_tested(
    service: VerificationService, session_factory: Factory
) -> None:
    route = respx.get(JOBS_URL)
    route.mock(return_value=httpx.Response(200, json=board_payload()))
    await service.verify_platform("greenhouse")
    route.mock(return_value=httpx.Response(200, json={"jobs": [], "meta": {"total": 0}}))
    result = await service.verify_platform("greenhouse")
    assert result.status_after is ImplementationStatus.TESTED
    assert (await _evidence(session_factory))[-1].error_message == "listagem vazia suspeita"


async def test_platform_without_connector_is_untouched(
    service: VerificationService, session_factory: Factory
) -> None:
    result = await service.verify_platform("adp")
    assert result.skipped_reason == "sem conector"
    assert (await _platform(session_factory, "adp")).implementation_status is (
        ImplementationStatus.NOT_STARTED
    )
    assert await _evidence(session_factory) == []


@respx.mock
async def test_reseed_does_not_overwrite_evidence_status(
    service: VerificationService, session_factory: Factory
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    await service.verify_platform("greenhouse")
    async with session_factory() as session:
        await PlatformRepository(session).upsert_catalog(build_catalog())
        await session.commit()
    platform = await _platform(session_factory, "greenhouse")
    assert platform.implementation_status is ImplementationStatus.VERIFIED
    assert platform.extraction_strategy and platform.docs_url and platform.notes


@respx.mock
async def test_coverage_report_counts_only_real_evidence(
    service: VerificationService, session_factory: Factory, tmp_path: Path
) -> None:
    async with session_factory() as session:
        before = await build_report(session)
    assert before.total == 78 and before.totals_by_status["VERIFIED"] == 0

    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    await service.verify_platform("greenhouse")
    async with session_factory() as session:
        report = await build_report(session, METADATA)
    assert report.totals_by_status["VERIFIED"] == 1
    assert report.totals_by_status["NOT_STARTED"] == 77
    gh = next(p for p in report.platforms if p.slug == "greenhouse")
    assert gh.jobs == 3 and gh.instances_verified == 1 and gh.instances_configured == 1
    assert "Senior Backend Engineer" in gh.sample_titles

    md_path, json_path = write_report(report, tmp_path)
    markdown = md_path.read_text(encoding="utf-8")
    assert "1 de 78 plataformas com evidencia real" in markdown
    assert "Senior Backend Engineer" in markdown and "## O que falta" in markdown
    assert markdown == render_markdown(report)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["total"] == 78 and len(payload["platforms"]) == 78
