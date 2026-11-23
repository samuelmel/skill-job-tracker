import asyncio
import uuid
from collections import defaultdict

from vaya_indexer.database.models import FetchRunStatus
from vaya_indexer.ingestion.fetch_all import SourceTarget, fetch_all
from vaya_indexer.ingestion.service import FetchRunSummary


def _target(platform: str, n: int) -> SourceTarget:
    return SourceTarget(source_id=uuid.uuid4(), platform=platform, external_id=f"{platform}-{n}")


def _summary(source_id: uuid.UUID, status: FetchRunStatus, **kw: object) -> FetchRunSummary:
    return FetchRunSummary(run_id=uuid.uuid4(), source_id=source_id, status=status, **kw)  # type: ignore[arg-type]


async def test_one_source_failure_is_isolated() -> None:
    targets = [_target("a", 1), _target("a", 2), _target("b", 1)]
    bad = targets[1].source_id

    async def run(source_id: uuid.UUID) -> FetchRunSummary:
        if source_id == bad:
            raise RuntimeError("boom")
        return _summary(source_id, FetchRunStatus.SUCCEEDED, jobs_found=5)

    report = await fetch_all(run, targets, concurrency=4)
    assert [o.status for o in report.outcomes] == [
        FetchRunStatus.SUCCEEDED,
        FetchRunStatus.FAILED,
        FetchRunStatus.SUCCEEDED,
    ]
    assert report.outcomes[1].error == "RuntimeError"  # so o tipo: a mensagem pode ter dados
    assert report.jobs_found == 10
    assert report.exit_code == 0


async def test_exit_code_nonzero_only_when_all_failed() -> None:
    targets = [_target("a", 1), _target("b", 1)]

    async def failing(source_id: uuid.UUID) -> FetchRunSummary:
        return _summary(source_id, FetchRunStatus.FAILED, error_message="x")

    assert (await fetch_all(failing, targets, concurrency=2)).exit_code == 1

    async def partial(source_id: uuid.UUID) -> FetchRunSummary:
        if source_id == targets[0].source_id:
            return _summary(source_id, FetchRunStatus.PARTIAL)
        return _summary(source_id, FetchRunStatus.FAILED)

    assert (await fetch_all(partial, targets, concurrency=2)).exit_code == 0
    assert (await fetch_all(failing, [], concurrency=2)).exit_code == 0


async def test_raw_failures_are_reported() -> None:
    target = _target("a", 1)

    async def run(source_id: uuid.UUID) -> FetchRunSummary:
        return _summary(source_id, FetchRunStatus.SUCCEEDED, raw_error="OSError: disco cheio")

    report = await fetch_all(run, [target], concurrency=1)
    assert report.raw_failures == 1 and report.outcomes[0].raw_error == "OSError: disco cheio"


async def test_at_most_one_concurrent_source_per_platform_and_global_limit() -> None:
    targets = [_target("a", i) for i in range(4)] + [_target("b", i) for i in range(4)]
    targets += [_target("c", i) for i in range(2)]
    platform_of = {t.source_id: t.platform for t in targets}
    running: defaultdict[str, int] = defaultdict(int)
    peak_platform = 0
    peak_global = 0

    async def run(source_id: uuid.UUID) -> FetchRunSummary:
        nonlocal peak_platform, peak_global
        platform = platform_of[source_id]
        running[platform] += 1
        peak_platform = max(peak_platform, running[platform])
        peak_global = max(peak_global, sum(running.values()))
        await asyncio.sleep(0.01)
        running[platform] -= 1
        return _summary(source_id, FetchRunStatus.SUCCEEDED)

    report = await fetch_all(run, targets, concurrency=2)
    assert len(report.outcomes) == 10
    assert peak_platform == 1
    assert peak_global == 2
