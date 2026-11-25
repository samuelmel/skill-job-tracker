"""Dedup entre fontes e reconciliacao segura, contra Postgres real e HTTP mockado (respx).

Payloads sinteticos no formato do Greenhouse: servem so para testes, nao como evidencia.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from skilometer.core.config import ProcessingPolicy
from skilometer.database.models import (
    DedupDecision,
    DedupMatch,
    FetchRunStatus,
    Job,
    JobHistory,
    JobSource,
    JobStatus,
)
from skilometer.extraction.http import HttpClient
from skilometer.ingestion.service import FetchRunSummary, IngestionService, register_source

pytestmark = pytest.mark.usefixtures("seeded")

Factory = async_sessionmaker[AsyncSession]
URL_A = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
URL_B = "https://boards-api.greenhouse.io/v1/boards/acme-careers/jobs"
WORDS = [f"palavra{i}" for i in range(80)]


def _desc(words: list[str]) -> str:
    return "&lt;p&gt;" + " ".join(words) + "&lt;/p&gt;"


FULL_DESC = _desc(WORDS[:60])


def _job(
    job_id: int,
    title: str,
    *,
    location: str = "Berlin, Germany",
    content: str = FULL_DESC,
    board: str = "acme",
) -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "updated_at": "2024-05-02T10:15:00Z",
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/{board}/jobs/{job_id}",
        "company_name": "Acme Inc.",
        "metadata": None,
        "content": content,
    }


def _payload(*jobs: dict[str, Any]) -> dict[str, Any]:
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


def _fillers(board: str, n: int = 2) -> list[dict[str, Any]]:
    return [
        _job(
            9000 + i,
            f"Filler Role {chr(65 + i)}",
            location=f"City{i}, Germany",
            board=board,
            content=_desc([f"{board}{i}w{k}" for k in range(40)]),
        )
        for i in range(n)
    ]


async def _noop_sleep(_: float) -> None:
    return None


@pytest.fixture
async def http() -> AsyncIterator[HttpClient]:
    async with HttpClient(user_agent="skilometer-test", max_retries=1, sleep=_noop_sleep) as client:
        yield client


@pytest.fixture
def service(session_factory: Factory, http: HttpClient) -> IngestionService:
    return IngestionService(session_factory, http, ProcessingPolicy(reconcile_grace_hours=0))


async def _register(session_factory: Factory, token: str) -> uuid.UUID:
    async with session_factory() as session:
        source = await register_source(
            session,
            platform_slug="greenhouse",
            company_name="Acme Inc.",
            config={"board_token": token},
        )
        await session.commit()
        return source.id


@pytest.fixture
async def src_a(session_factory: Factory) -> uuid.UUID:
    return await _register(session_factory, "acme")


@pytest.fixture
async def src_b(session_factory: Factory) -> uuid.UUID:
    return await _register(session_factory, "acme-careers")


async def _count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


def _mock(url: str, *jobs: dict[str, Any]) -> respx.Route:
    return respx.get(url).mock(return_value=httpx.Response(200, json=_payload(*jobs)))


async def _statuses(
    session_factory: Factory,
) -> tuple[list[JobStatus], list[tuple[JobStatus, int]]]:
    async with session_factory() as session:
        jobs = list(await session.scalars(select(Job.status)))
        sources = [
            (s.status, s.missing_count)
            for s in await session.scalars(select(JobSource).order_by(JobSource.external_job_id))
        ]
    return jobs, sources


# ---------------------------------------------------------------- dedup


@respx.mock
async def test_same_job_in_two_sources_is_merged(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    _mock(URL_A, _job(1, "Senior Backend Engineer"))
    _mock(URL_B, _job(77, "Senior Backend Engineer", board="acme-careers"))
    await service.run_source(src_a)
    summary = await service.run_source(src_b)

    assert summary.jobs_inserted == 1
    async with session_factory() as session:
        assert await _count(session, Job) == 1
        assert await _count(session, JobSource) == 2
        match = await session.scalar(select(DedupMatch))
        assert match is not None and match.decision is DedupDecision.MERGED
        assert match.job_id_b is None and match.job_source_id is not None
        assert match.score >= 0.9 and match.method == "fingerprint+description"
        assert match.reasons
        sources = list(await session.scalars(select(JobSource)))
        assert len({s.job_id for s in sources}) == 1


@respx.mock
async def test_same_title_in_different_cities_stays_separate(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    _mock(URL_A, _job(1, "Senior Backend Engineer", location="Berlin, Germany"))
    _mock(URL_B, _job(77, "Senior Backend Engineer", location="Munich, Germany", board="x"))
    await service.run_source(src_a)
    await service.run_source(src_b)
    async with session_factory() as session:
        assert await _count(session, Job) == 2
        assert await _count(session, DedupMatch) == 0


@respx.mock
async def test_intermediate_score_creates_job_and_review(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    partial = _desc(WORDS[:40] + WORDS[60:80])
    _mock(URL_A, _job(1, "Senior Backend Engineer"))
    _mock(URL_B, _job(77, "Senior Backend Engineer", content=partial, board="acme-careers"))
    await service.run_source(src_a)
    await service.run_source(src_b)
    async with session_factory() as session:
        assert await _count(session, Job) == 2
        assert await _count(session, JobSource) == 2
        match = await session.scalar(select(DedupMatch))
        assert match is not None and match.decision is DedupDecision.REVIEW
        assert match.job_id_b is not None and match.job_id_a != match.job_id_b
        assert 0.75 <= match.score < 0.90


@respx.mock
async def test_dedup_is_idempotent_across_runs(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    _mock(URL_A, _job(1, "Senior Backend Engineer"))
    _mock(URL_B, _job(77, "Senior Backend Engineer", board="acme-careers"))
    for _ in range(2):
        await service.run_source(src_a)
        again = await service.run_source(src_b)
    assert (again.jobs_inserted, again.jobs_updated, again.jobs_unchanged) == (0, 0, 1)
    async with session_factory() as session:
        assert await _count(session, Job) == 1
        assert await _count(session, JobSource) == 2
        assert await _count(session, DedupMatch) == 1


@respx.mock
async def test_same_source_ids_are_never_merged(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    _mock(URL_A, _job(1, "Senior Backend Engineer"), _job(2, "Senior Backend Engineer"))
    await service.run_source(src_a)
    async with session_factory() as session:
        assert await _count(session, Job) == 2
        assert await _count(session, DedupMatch) == 0


@respx.mock
async def test_non_primary_source_only_fills_nulls(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    first = _job(1, "Senior Backend Engineer")
    second = _job(77, "Senior Backend Engineer", board="acme-careers")
    second["metadata"] = [{"name": "Employment Type", "value": "Full-time"}]
    _mock(URL_A, first)
    route_b = _mock(URL_B, second)
    await service.run_source(src_a)
    await service.run_source(src_b)
    async with session_factory() as session:
        job = await session.scalar(select(Job))
        assert job is not None and job.employment_type == "FULL_TIME"  # NULL preenchido
    changed = dict(second, title="Sr Backend Engineer", location="Hamburg, Germany")
    route_b.mock(return_value=httpx.Response(200, json=_payload(changed)))
    await service.run_source(src_b)
    async with session_factory() as session:
        job = await session.scalar(select(Job))
        assert job is not None and job.title == "Senior Backend Engineer"
        assert job.location == "Berlin, Germany"


# ------------------------------------------------------- reconciliation


async def _run_many(service: IngestionService, source_id: uuid.UUID, n: int) -> FetchRunSummary:
    summary = None
    for _ in range(n):
        summary = await service.run_source(source_id)
    assert summary is not None
    return summary


@respx.mock
async def test_absent_once_stays_open_twice_closes(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    jobs = [_job(1, "Alpha Role", location="Rome, Italy"), *_fillers("acme", 2)]
    route = _mock(URL_A, *jobs)
    await service.run_source(src_a)
    route.mock(return_value=httpx.Response(200, json=_payload(*jobs[1:])))

    first = await service.run_source(src_a)
    assert first.jobs_closed == 0 and first.status is FetchRunStatus.SUCCEEDED
    async with session_factory() as session:
        gone = await session.scalar(select(JobSource).where(JobSource.external_job_id == "1"))
        assert gone is not None and gone.status is JobStatus.OPEN and gone.missing_count == 1

    second = await service.run_source(src_a)
    assert second.jobs_closed == 1 and second.status is FetchRunStatus.SUCCEEDED
    async with session_factory() as session:
        gone = await session.scalar(select(JobSource).where(JobSource.external_job_id == "1"))
        assert gone is not None and gone.status is JobStatus.CLOSED
        job = await session.get(Job, gone.job_id)
        assert job is not None and job.status is JobStatus.CLOSED
        fields = set(
            await session.scalars(
                select(JobHistory.field_name).where(JobHistory.fetch_run_id == second.run_id)
            )
        )
        assert {"status", "source_status"} <= fields
        open_jobs = await session.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.OPEN)
        )
        assert open_jobs == 2  # nenhum outro job foi fechado


@respx.mock
async def test_two_source_job_closes_only_when_both_sources_close(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    shared_a = _job(1, "Senior Backend Engineer")
    shared_b = _job(77, "Senior Backend Engineer", board="acme-careers")
    route_a = _mock(URL_A, shared_a, *_fillers("acme"))
    route_b = _mock(URL_B, shared_b, *_fillers("acme-careers"))
    await service.run_source(src_a)
    await service.run_source(src_b)
    route_a.mock(return_value=httpx.Response(200, json=_payload(*_fillers("acme"))))

    await service.run_source(src_a)
    closed = await service.run_source(src_a)
    assert closed.jobs_closed == 0  # so a fonte A fechou; o job segue OPEN via B
    async with session_factory() as session:
        shared = await session.scalar(select(Job).where(Job.title == "Senior Backend Engineer"))
        assert shared is not None and shared.status is JobStatus.OPEN
        a_source = await session.scalar(select(JobSource).where(JobSource.external_job_id == "1"))
        assert a_source is not None and a_source.status is JobStatus.CLOSED

    route_b.mock(return_value=httpx.Response(200, json=_payload(*_fillers("acme-careers"))))
    await service.run_source(src_b)
    final = await service.run_source(src_b)
    assert final.jobs_closed == 1
    async with session_factory() as session:
        shared = await session.scalar(select(Job).where(Job.title == "Senior Backend Engineer"))
        assert shared is not None and shared.status is JobStatus.CLOSED


@respx.mock
async def test_failed_run_and_incomplete_listing_change_nothing(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    jobs = [_job(1, "Alpha Role", location="Rome, Italy"), *_fillers("acme", 2)]
    route = _mock(URL_A, *jobs)
    await service.run_source(src_a)

    route.mock(return_value=httpx.Response(503))
    failed = await service.run_source(src_a)
    assert failed.status is FetchRunStatus.FAILED

    incomplete = _payload(*jobs[1:])
    incomplete["meta"]["total"] = 99  # listagem incompleta
    route.mock(return_value=httpx.Response(200, json=incomplete))
    partial = await service.run_source(src_a)
    assert partial.status is FetchRunStatus.PARTIAL
    assert partial.error_message == "listagem incompleta" and partial.jobs_closed == 0

    jobs_status, sources = await _statuses(session_factory)
    assert set(jobs_status) == {JobStatus.OPEN}
    assert all(s == (JobStatus.OPEN, 0) for s in sources)


@respx.mock
async def test_suspicious_empty_listing_closes_nothing(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    route = _mock(URL_A, *_fillers("acme", 2))
    await service.run_source(src_a)
    route.mock(return_value=httpx.Response(200, json=_payload()))
    summary = await _run_many(service, src_a, 3)

    assert summary.status is FetchRunStatus.PARTIAL
    assert summary.error_message == "listagem vazia suspeita" and summary.jobs_closed == 0
    jobs_status, sources = await _statuses(session_factory)
    assert set(jobs_status) == {JobStatus.OPEN}
    assert all(s == (JobStatus.OPEN, 0) for s in sources)


@respx.mock
async def test_high_absent_ratio_closes_nothing(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    jobs = [_job(i, f"Role Number{chr(65 + i)}", location=f"City{i}, Italy") for i in range(12)]
    route = _mock(URL_A, *jobs)
    await service.run_source(src_a)
    route.mock(return_value=httpx.Response(200, json=_payload(jobs[0], jobs[1])))  # 10/12 ausentes
    summary = await _run_many(service, src_a, 3)

    assert summary.status is FetchRunStatus.PARTIAL and summary.jobs_closed == 0
    assert summary.error_message is not None and "acima do limite" in summary.error_message
    jobs_status, sources = await _statuses(session_factory)
    assert set(jobs_status) == {JobStatus.OPEN}
    assert all(s == (JobStatus.OPEN, 0) for s in sources)


@respx.mock
async def test_reappearing_job_reopens(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    jobs = [_job(1, "Alpha Role", location="Rome, Italy"), *_fillers("acme", 2)]
    route = _mock(URL_A, *jobs)
    await service.run_source(src_a)
    route.mock(return_value=httpx.Response(200, json=_payload(*jobs[1:])))
    await _run_many(service, src_a, 2)

    route.mock(return_value=httpx.Response(200, json=_payload(*jobs)))
    back = await service.run_source(src_a)
    assert back.jobs_updated == 1 and back.jobs_inserted == 0
    async with session_factory() as session:
        js = await session.scalar(select(JobSource).where(JobSource.external_job_id == "1"))
        assert js is not None and js.status is JobStatus.OPEN and js.missing_count == 0
        job = await session.get(Job, js.job_id)
        assert job is not None and job.status is JobStatus.OPEN
        reopened = list(
            await session.scalars(
                select(JobHistory).where(
                    JobHistory.fetch_run_id == back.run_id, JobHistory.field_name == "status"
                )
            )
        )
        assert [(h.old_value, h.new_value) for h in reopened] == [("CLOSED", "OPEN")]


@respx.mock
async def test_small_board_mostly_absent_still_closes(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    old = [_job(i, f"Old Role {chr(65 + i)}", location=f"City{i}, Italy") for i in range(3)]
    route = _mock(URL_A, *old)
    await service.run_source(src_a)
    new = _job(50, "Brand New Role", location="Oslo, Norway")
    route.mock(return_value=httpx.Response(200, json=_payload(new)))  # 3 de 4 abertas ausentes
    first = await service.run_source(src_a)
    second = await service.run_source(src_a)
    assert first.jobs_closed == 0 and second.jobs_closed == 3
    assert second.status is FetchRunStatus.SUCCEEDED


@respx.mock
async def test_candidate_limit_keeps_exact_match(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    from skilometer.database.repositories.jobs import JobRepository

    prefixed = [
        _job(i, f"Data Engineer {chr(65 + i)}", location=f"City{i}, Italy") for i in range(1, 5)
    ]
    exact = _job(20, "Data Engineer", location="Rome, Italy")
    _mock(URL_A, *prefixed, exact)
    await service.run_source(src_a)
    async with session_factory() as session:
        target = await session.scalar(select(Job).where(Job.title == "Data Engineer"))
        assert target is not None
        found = await JobRepository(session).find_dedup_candidates(
            company_id=target.company_id,
            fingerprint="nao-existe",
            normalized_title="data engineer",
            canonical_urls=[],
            limit=2,
        )
        assert target.id in {j.id for j in found} and len(found) == 2


@respx.mock
async def test_concurrent_run_is_rejected_without_touching_data(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID
) -> None:
    from sqlalchemy import text

    from skilometer.ingestion.service import CONCURRENT_MESSAGE, source_lock_key

    _mock(URL_A, _job(1, "Alpha Role"))
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": source_lock_key(src_a)}
        )
        blocked = await service.run_source(src_a)
        assert blocked.status is FetchRunStatus.FAILED
        assert blocked.error_message == CONCURRENT_MESSAGE
    async with session_factory() as session:
        assert await _count(session, Job) == 0
    ok = await service.run_source(src_a)  # lock liberado
    assert ok.status is FetchRunStatus.SUCCEEDED and ok.jobs_inserted == 1


@respx.mock
async def test_generic_shared_url_with_different_titles_does_not_merge(
    session_factory: Factory, service: IngestionService, src_a: uuid.UUID, src_b: uuid.UUID
) -> None:
    a = _job(1, "Senior Backend Engineer")
    b = _job(77, "Office Manager", board="acme-careers")
    a["absolute_url"] = b["absolute_url"] = "https://acme.com/careers"
    _mock(URL_A, a)
    _mock(URL_B, b)
    await service.run_source(src_a)
    await service.run_source(src_b)
    async with session_factory() as session:
        assert await _count(session, Job) == 2
