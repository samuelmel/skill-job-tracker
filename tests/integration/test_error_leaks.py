"""Log e stdout publicos (Actions) nao podem carregar conteudo de vaga. Dados sinteticos."""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload
from vaya_indexer.connectors.base import NormalizedJob, RawJob
from vaya_indexer.connectors.greenhouse import GreenhouseConnector
from vaya_indexer.core.exceptions import safe_error
from vaya_indexer.database.models import FetchRunStatus, Job
from vaya_indexer.database.session import create_engine
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.ingestion.service import IngestionService, register_source

pytestmark = pytest.mark.usefixtures("seeded")

Factory = async_sessionmaker[AsyncSession]
SECRET = "descricao-sintetica pessoa.teste@exemplo.invalid telefone 555-0100"


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


def _everything(caplog: pytest.LogCaptureFixture) -> str:
    """Texto formatado e todos os campos extras dos registros (o JSON de log os imprime)."""
    return caplog.text + " ".join(str(r.__dict__) for r in caplog.records)


def test_engine_hides_parameters(migrated_url: str) -> None:
    assert create_engine(migrated_url).sync_engine.hide_parameters is True


async def test_db_error_text_has_value_but_safe_error_does_not(session_factory: Factory) -> None:
    async with session_factory() as session:
        with pytest.raises(DBAPIError) as info:
            await session.execute(text("select cast(:v as integer)"), {"v": SECRET})
    assert "pessoa.teste@exemplo.invalid" in str(info.value.orig)  # o servidor devolve o valor
    assert SECRET not in safe_error(info.value)


@respx.mock
async def test_record_and_fetch_failures_do_not_log_content(
    session_factory: Factory,
    http: HttpClient,
    source_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    original = GreenhouseConnector.normalize

    def boom(self: Any, raw: RawJob) -> NormalizedJob:
        if raw.external_id == "1001":
            raise ValueError(SECRET)
        return original(self, raw)

    monkeypatch.setattr(GreenhouseConnector, "normalize", boom)
    with caplog.at_level(logging.DEBUG):
        summary = await IngestionService(session_factory, http).run_source(source_id)
    assert summary.records_failed == 1
    assert SECRET not in _everything(caplog) and "pessoa.teste" not in _everything(caplog)
    assert any(getattr(r, "error", None) == "ValueError" for r in caplog.records)

    caplog.clear()

    async def fail(*_: Any, **__: Any) -> Any:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(GreenhouseConnector, "fetch_jobs", fail)
    with caplog.at_level(logging.DEBUG):
        failed = await IngestionService(session_factory, http).run_source(source_id)
    assert failed.status is FetchRunStatus.FAILED
    assert SECRET not in _everything(caplog) and SECRET not in (failed.error_message or "")
    assert failed.error_message == "RuntimeError"


@respx.mock
async def test_nul_and_long_employment_type_do_not_lose_the_job(
    session_factory: Factory, http: HttpClient, source_id: uuid.UUID
) -> None:
    payload = board_payload()
    payload["jobs"][0]["content"] = "&lt;p&gt;texto\x00com nul&lt;/p&gt;"
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=payload))
    summary = await IngestionService(session_factory, http).run_source(source_id)
    assert summary.records_failed == 0 and summary.jobs_inserted == 3
    async with session_factory() as session:
        job = await session.scalar(select(Job).where(Job.title == "Senior Backend Engineer"))
        assert job is not None and "\x00" not in (job.description or "")
        assert job.description == "<p>textocom nul</p>"


def test_normalized_job_caps_employment_type_and_strips_nul() -> None:
    job = NormalizedJob(title="T\x00x", employment_type="f" * 90, description="a\x00b")
    assert job.title == "Tx" and job.description == "ab"
    assert job.employment_type is not None and len(job.employment_type) == 60
