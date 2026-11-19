from datetime import UTC, datetime, timedelta

from vaya_indexer.core.config import ProcessingPolicy
from vaya_indexer.ingestion.reconciliation import (
    EMPTY_LISTING_MESSAGE,
    ReconcileAction,
    decide_reconciliation,
    should_close,
)

POLICY = ProcessingPolicy()


def _decide(**kw: object) -> ReconcileAction:
    base: dict[str, object] = {
        "complete": True,
        "records_failed": 0,
        "jobs_found": 8,
        "open_count": 10,
        "missing_count": 2,
        "policy": POLICY,
    }
    return decide_reconciliation(**{**base, **kw}).action  # type: ignore[arg-type]


def test_normal_run_applies() -> None:
    assert _decide() is ReconcileAction.APPLY


def test_incomplete_or_failed_records_skip() -> None:
    assert _decide(complete=False) is ReconcileAction.SKIP
    assert _decide(records_failed=1) is ReconcileAction.SKIP
    assert _decide(open_count=0, jobs_found=0) is ReconcileAction.SKIP


def test_empty_listing_with_open_jobs_blocks() -> None:
    decision = decide_reconciliation(
        complete=True,
        records_failed=0,
        jobs_found=0,
        open_count=5,
        missing_count=5,
        policy=POLICY,
    )
    assert decision.action is ReconcileAction.BLOCK
    assert decision.message == EMPTY_LISTING_MESSAGE


def test_high_ratio_blocks_and_boundary_applies() -> None:
    assert _decide(open_count=10, missing_count=6, jobs_found=4) is ReconcileAction.BLOCK
    assert _decide(open_count=10, missing_count=5, jobs_found=5) is ReconcileAction.APPLY


def test_should_close_needs_misses_and_grace() -> None:
    now = datetime(2025, 1, 2, tzinfo=UTC)
    old = now - timedelta(hours=13)
    recent = now - timedelta(hours=1)
    assert should_close(missing_count=2, last_seen_at=old, now=now, policy=POLICY)
    assert not should_close(missing_count=1, last_seen_at=old, now=now, policy=POLICY)
    assert not should_close(missing_count=2, last_seen_at=recent, now=now, policy=POLICY)
    zero = ProcessingPolicy(reconcile_grace_hours=0)
    assert should_close(missing_count=2, last_seen_at=now, now=now, policy=zero)


def test_small_boards_are_not_blocked_forever() -> None:
    assert _decide(open_count=1, missing_count=1, jobs_found=1) is ReconcileAction.APPLY
    assert _decide(open_count=3, missing_count=2, jobs_found=1) is ReconcileAction.APPLY
    assert _decide(open_count=100, missing_count=60, jobs_found=40) is ReconcileAction.BLOCK
    assert _decide(open_count=100, missing_count=50, jobs_found=50) is ReconcileAction.APPLY
