"""Reconciliacao segura de vagas ausentes (politica pura + aplicacao no banco)."""

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.core.config import ProcessingPolicy
from skilometer.database.models import JobSource, JobStatus
from skilometer.database.repositories.jobs import JobRepository
from skilometer.ingestion.job_status import refresh_job_status

EMPTY_LISTING_MESSAGE = "listagem vazia suspeita"


class ReconcileAction(enum.StrEnum):
    SKIP = "SKIP"  # run nao elegivel: nao toca em nada
    BLOCK = "BLOCK"  # guarda disparou: nao toca em nada e a run fica PARTIAL
    APPLY = "APPLY"


@dataclass(frozen=True)
class ReconcileDecision:
    action: ReconcileAction
    message: str | None = None


def decide_reconciliation(
    *,
    complete: bool,
    records_failed: int,
    jobs_found: int,
    open_count: int,
    missing_count: int,
    policy: ProcessingPolicy,
) -> ReconcileDecision:
    """Decide se a reconciliacao roda. Chamar so para runs sem excecao."""
    if not complete or records_failed > 0 or open_count == 0:
        return ReconcileDecision(ReconcileAction.SKIP)
    if jobs_found == 0:
        return ReconcileDecision(ReconcileAction.BLOCK, EMPTY_LISTING_MESSAGE)
    ratio = missing_count / open_count
    # Boards pequenos: a guarda so vale acima de um piso absoluto (senao bloquearia para sempre).
    limit = max(policy.reconcile_min_block_count, policy.reconcile_max_close_ratio * open_count)
    if missing_count > limit:
        return ReconcileDecision(
            ReconcileAction.BLOCK,
            f"ausentes {missing_count}/{open_count} ({ratio:.0%}) acima do limite "
            f"{policy.reconcile_max_close_ratio:.0%}: nada foi fechado",
        )
    return ReconcileDecision(ReconcileAction.APPLY)


def should_close(
    *, missing_count: int, last_seen_at: datetime, now: datetime, policy: ProcessingPolicy
) -> bool:
    grace = timedelta(hours=policy.reconcile_grace_hours)
    return missing_count >= policy.reconcile_misses_to_close and last_seen_at <= now - grace


@dataclass
class ReconcileOutcome:
    jobs_closed: int = 0
    message: str | None = None
    blocked: bool = False


async def apply_reconciliation(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    seen_external_ids: set[str],
    jobs_found: int,
    complete: bool,
    records_failed: int,
    now: datetime,
    policy: ProcessingPolicy,
) -> ReconcileOutcome:
    """Incrementa missing_count dos ausentes e fecha os elegiveis, respeitando as guardas.

    Deve ser chamada so quando a run nao levantou excecao. Os vistos ja tem missing_count=0
    (zerado na persistencia).
    """
    repo = JobRepository(session)
    open_sources = await repo.open_job_sources(source_id)
    missing = [js for js in open_sources if js.external_job_id not in seen_external_ids]
    decision = decide_reconciliation(
        complete=complete,
        records_failed=records_failed,
        jobs_found=jobs_found,
        open_count=len(open_sources),
        missing_count=len(missing),
        policy=policy,
    )
    if decision.action is ReconcileAction.BLOCK:
        return ReconcileOutcome(message=decision.message, blocked=True)
    if decision.action is ReconcileAction.SKIP:
        return ReconcileOutcome()

    touched_jobs: dict[uuid.UUID, None] = {}
    for js in missing:
        js.missing_count += 1
        if should_close(
            missing_count=js.missing_count, last_seen_at=js.last_seen_at, now=now, policy=policy
        ):
            _close_job_source(repo, js, source_id, run_id, now)
            touched_jobs[js.job_id] = None
    await session.flush()
    closed = 0
    for job_id in touched_jobs:
        if (
            await refresh_job_status(session, job_id, source_id=source_id, run_id=run_id, now=now)
            is JobStatus.CLOSED
        ):
            closed += 1
    return ReconcileOutcome(jobs_closed=closed)


def _close_job_source(
    repo: JobRepository, js: JobSource, source_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> None:
    repo.add_history(
        job_id=js.job_id,
        source_id=source_id,
        fetch_run_id=run_id,
        field_name="source_status",
        old_value=js.status.value,
        new_value=JobStatus.CLOSED.value,
        detected_at=now,
    )
    js.status = JobStatus.CLOSED
