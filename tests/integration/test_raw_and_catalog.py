import json
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from skilometer.connectors.base import RawJob
from skilometer.database.models import FetchRunStatus, Job, Source
from skilometer.extraction.http import HttpClient
from skilometer.ingestion.fetch_all import fetch_all, list_active_targets
from skilometer.ingestion.service import IngestionService, register_source
from skilometer.lake.raw import RawLakeWriter, RawWriteResult
from skilometer.source_catalog import CatalogEntry, SourceCatalog, load_catalog
from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload

pytestmark = pytest.mark.usefixtures("seeded")

Factory = async_sessionmaker[AsyncSession]


async def _noop_sleep(_: float) -> None:
    return None


@pytest.fixture
async def http() -> AsyncIterator[HttpClient]:
    async with HttpClient(user_agent="skilometer-test", max_retries=1, sleep=_noop_sleep) as client:
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


class BrokenWriter:
    def write(self, **_: object) -> RawWriteResult:
        raise OSError("disco cheio")


@respx.mock
async def test_ingestion_writes_raw_before_persisting(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID, tmp_path: Path
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    service = IngestionService(session_factory, http, raw_writer=RawLakeWriter(tmp_path))
    summary = await service.run_source(source_id)

    assert summary.status is FetchRunStatus.SUCCEEDED and summary.raw_error is None
    assert summary.raw_path is not None
    manifest = json.loads((Path(summary.raw_path) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["job_count"] == summary.jobs_found == 3
    assert manifest["run_id"] == str(summary.run_id)
    assert manifest["platform"] == "greenhouse"
    assert manifest["connector"] == "GreenhouseConnector"
    assert manifest["complete"] is True


@respx.mock
async def test_raw_failure_does_not_block_ingestion(
    session_factory: Factory,
    http: HttpClient,
    source_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    service = IngestionService(session_factory, http, raw_writer=BrokenWriter())  # type: ignore[arg-type]
    with caplog.at_level("ERROR", logger="skilometer.ingestion"):
        summary = await service.run_source(source_id)

    assert summary.status is FetchRunStatus.SUCCEEDED
    assert summary.jobs_inserted == 3
    assert summary.raw_path is None
    assert summary.raw_error == "OSError"  # so o tipo, nunca a mensagem
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 3
    record = next(r for r in caplog.records if r.message == "raw_write_failed")
    assert record.source_id == str(source_id)  # type: ignore[attr-defined]
    assert record.run_id == str(summary.run_id)  # type: ignore[attr-defined]
    assert record.platform == "greenhouse"  # type: ignore[attr-defined]


@respx.mock
async def test_no_writer_keeps_behaviour(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    summary = await IngestionService(session_factory, http).run_source(source_id)
    assert summary.status is FetchRunStatus.SUCCEEDED
    assert summary.raw_path is None and summary.raw_error is None


class _CapturingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int]] = []

    def write(
        self,
        *,
        platform: str,
        source_external_id: str,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        fetched_at: datetime,
        complete: bool,
        jobs: Sequence[RawJob],
        connector: str,
    ) -> RawWriteResult:
        self.calls.append((platform, complete, len(jobs)))
        return RawWriteResult(path="mem", job_count=len(jobs), jobs_sha256="0")


@respx.mock
async def test_fetch_all_isolates_failing_source_end_to_end(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        await register_source(
            session, platform_slug="greenhouse", company_name="Beta", config={"board_token": "beta"}
        )
        await session.commit()
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    respx.get("https://boards-api.greenhouse.io/v1/boards/beta/jobs").mock(
        return_value=httpx.Response(500)
    )
    writer = _CapturingWriter()
    service = IngestionService(session_factory, http, raw_writer=writer)  # type: ignore[arg-type]
    targets = await list_active_targets(session_factory)
    assert [t.external_id for t in targets] == ["acme", "beta"]
    report = await fetch_all(service.run_source, targets, concurrency=3)

    by_ext = {o.target.external_id: o for o in report.outcomes}
    assert by_ext["acme"].status is FetchRunStatus.SUCCEEDED
    assert by_ext["beta"].status is FetchRunStatus.FAILED
    assert report.exit_code == 0
    assert writer.calls == [("greenhouse", True, 3)]


async def test_list_active_targets_filters_and_limits(session_factory: Factory) -> None:
    async with session_factory() as session:
        for token in ("a", "b", "c"):
            await register_source(
                session,
                platform_slug="greenhouse",
                company_name=token,
                config={"board_token": token},
            )
        await register_source(
            session, platform_slug="lever-co", company_name="L", config={"company": "l"}
        )
        disabled = await session.scalar(select(Source).where(Source.external_id == "c"))
        assert disabled is not None
        disabled.enabled = False
        await session.commit()
    targets = await list_active_targets(session_factory)
    assert sorted(t.external_id for t in targets) == ["a", "b", "l"]
    assert len(await list_active_targets(session_factory, limit=2)) == 2
    only = await list_active_targets(session_factory, platform="lever-co")
    assert [t.external_id for t in only] == ["l"]


async def test_catalog_load_is_idempotent(session_factory: Factory) -> None:
    catalog = SourceCatalog(
        sources=[
            CatalogEntry(platform="greenhouse", company="Acme", config={"board_token": "acme"}),
            CatalogEntry(platform="greenhouse", company="Beta", config={"board_token": "beta"}),
            CatalogEntry(platform="nao-existe", company="X", config={"a": 1}),
        ]
    )
    async with session_factory() as session:
        first = await load_catalog(session, catalog)
    assert (first.total, first.created, first.existing, len(first.errors)) == (3, 2, 0, 1)
    async with session_factory() as session:
        second = await load_catalog(session, catalog)
        count = await session.scalar(select(func.count()).select_from(Source))
    assert (second.created, second.existing, len(second.errors)) == (0, 2, 1)
    assert count == 2


def test_committed_catalog_is_valid() -> None:
    from skilometer.source_catalog import read_catalog

    root = Path(__file__).resolve().parents[2]
    catalog = read_catalog(root / "catalog" / "sources.json")
    assert len(catalog.sources) > 0
    keys = {(e.platform, json.dumps(e.config, sort_keys=True)) for e in catalog.sources}
    assert len(keys) == len(catalog.sources)
