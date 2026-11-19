import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vaya_indexer.database.models import Company, Job, JobHistory, JobSource


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def job_sources_by_external_id(self, source_id: uuid.UUID) -> dict[str, JobSource]:
        rows = await self.session.scalars(select(JobSource).where(JobSource.source_id == source_id))
        return {js.external_job_id: js for js in rows}

    async def get_job(self, job_id: uuid.UUID) -> Job:
        job = await self.session.get(Job, job_id)
        assert job is not None
        return job

    def add_job(self, **fields: Any) -> Job:
        job = Job(**fields)
        self.session.add(job)
        return job

    def add_job_source(self, **fields: Any) -> JobSource:
        job_source = JobSource(**fields)
        self.session.add(job_source)
        return job_source

    def add_history(
        self,
        *,
        job_id: uuid.UUID,
        source_id: uuid.UUID,
        fetch_run_id: uuid.UUID,
        field_name: str,
        old_value: Any,
        new_value: Any,
        detected_at: datetime,
    ) -> None:
        self.session.add(
            JobHistory(
                job_id=job_id,
                source_id=source_id,
                fetch_run_id=fetch_run_id,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                detected_at=detected_at,
            )
        )

    async def list_recent(self, limit: int) -> Sequence[tuple[Job, str]]:
        stmt = (
            select(Job, Company.name)
            .join(Company, Company.id == Job.company_id)
            .order_by(Job.first_seen_at.desc(), Job.title)
            .limit(limit)
        )
        return [(job, company) for job, company in (await self.session.execute(stmt)).all()]
