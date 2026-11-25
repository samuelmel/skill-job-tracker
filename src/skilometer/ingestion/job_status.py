"""Status do job canonico derivado dos seus job_sources."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from skilometer.database.models import JobStatus
from skilometer.database.repositories.jobs import JobRepository


def derive_job_status(source_statuses: list[JobStatus], current: JobStatus) -> JobStatus:
    """OPEN se qualquer fonte esta OPEN; CLOSED so se todas CLOSED; senao mantem o atual."""
    if JobStatus.OPEN in source_statuses:
        return JobStatus.OPEN
    if source_statuses and all(s is JobStatus.CLOSED for s in source_statuses):
        return JobStatus.CLOSED
    return current


async def refresh_job_status(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    now: datetime,
) -> JobStatus | None:
    """Recalcula o status do job; se mudou, grava job_history (field_name='status').

    Devolve o novo status quando houve mudanca, senao None.
    """
    repo = JobRepository(session)
    job = await repo.get_job(job_id)
    new = derive_job_status(await repo.job_source_statuses(job_id), job.status)
    if new is job.status:
        return None
    repo.add_history(
        job_id=job_id,
        source_id=source_id,
        fetch_run_id=run_id,
        field_name="status",
        old_value=job.status.value,
        new_value=new.value,
        detected_at=now,
    )
    job.status = new
    return new
