import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vaya_indexer.database.models import (
    Company,
    DedupDecision,
    DedupMatch,
    Job,
    JobHistory,
    JobSource,
    JobStatus,
)


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

    async def job_source_statuses(self, job_id: uuid.UUID) -> list[JobStatus]:
        rows = await self.session.scalars(
            select(JobSource.status).where(JobSource.job_id == job_id)
        )
        return list(rows)

    async def open_job_sources(self, source_id: uuid.UUID) -> list[JobSource]:
        rows = await self.session.scalars(
            select(JobSource).where(
                JobSource.source_id == source_id, JobSource.status == JobStatus.OPEN
            )
        )
        return list(rows)

    async def primary_job_source_id(self, job_id: uuid.UUID) -> uuid.UUID | None:
        """Primeira fonte do job (a que define os campos canonicos)."""
        return await self.session.scalar(
            select(JobSource.id)
            .where(JobSource.job_id == job_id)
            .order_by(JobSource.first_seen_at, JobSource.created_at, JobSource.id)
            .limit(1)
        )

    async def find_dedup_candidates(
        self,
        *,
        company_id: uuid.UUID,
        fingerprint: str,
        normalized_title: str,
        canonical_urls: Sequence[str],
        limit: int,
    ) -> list[Job]:
        """Candidatos de dedup: mesma empresa (indexado) + fingerprint/URL/titulo igual ou prefixo.

        Nunca varre a tabela inteira: o filtro de empresa e obrigatorio e o resultado tem LIMIT.
        """
        url_match = (
            select(JobSource.job_id).where(
                or_(
                    JobSource.canonical_url.in_(canonical_urls),
                    JobSource.canonical_application_url.in_(canonical_urls),
                )
            )
            if canonical_urls
            else None
        )
        conditions = [
            Job.canonical_fingerprint == fingerprint,
            Job.normalized_title == normalized_title,
            func.starts_with(Job.normalized_title, normalized_title),
            func.starts_with(normalized_title, Job.normalized_title),
        ]
        if url_match is not None:
            conditions.append(Job.id.in_(url_match))
        strong = [Job.canonical_fingerprint == fingerprint]
        if url_match is not None:
            strong.append(Job.id.in_(url_match))
        # Evidencia forte primeiro (fingerprint/URL), depois titulo igual, por fim prefixo;
        # o LIMIT nunca descarta um match exato em favor de prefixos.
        priority = case(
            (or_(*strong), 0),
            (Job.normalized_title == normalized_title, 1),
            else_=2,
        )
        stmt = (
            select(Job)
            .where(Job.company_id == company_id, Job.normalized_title != "", or_(*conditions))
            .order_by(priority, Job.last_seen_at.desc(), Job.id)
            .limit(limit)
        )
        return list(await self.session.scalars(stmt))

    async def job_sources_for_jobs(self, job_ids: Sequence[uuid.UUID]) -> list[JobSource]:
        if not job_ids:
            return []
        rows = await self.session.scalars(select(JobSource).where(JobSource.job_id.in_(job_ids)))
        return list(rows)

    def add_dedup_match(
        self,
        *,
        job_id_a: uuid.UUID,
        job_id_b: uuid.UUID | None,
        job_source_id: uuid.UUID | None,
        score: float,
        method: str,
        reasons: Sequence[str],
        decision: DedupDecision,
    ) -> None:
        self.session.add(
            DedupMatch(
                job_id_a=job_id_a,
                job_id_b=job_id_b,
                job_source_id=job_source_id,
                score=score,
                method=method,
                reasons=list(reasons),
                decision=decision,
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
