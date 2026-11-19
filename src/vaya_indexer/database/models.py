"""Modelos SQLAlchemy 2.0. O schema versionado e o da migration (migrations/versions)."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import CHAR, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from vaya_indexer.database.base import Base, TimestampMixin, uuid_pk


class PlatformCategory(enum.StrEnum):
    ATS = "ATS"
    JOB_BOARD = "JOB_BOARD"


class ImplementationStatus(enum.StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RESEARCHING = "RESEARCHING"
    IMPLEMENTED = "IMPLEMENTED"
    TESTED = "TESTED"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"


class JobStatus(enum.StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class FetchRunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DedupDecision(enum.StrEnum):
    MERGED = "MERGED"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


def pg_enum(cls: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(cls, name=name, native_enum=True)


def tstz(**kwargs: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), **kwargs)


def fk(target: str, ondelete: str, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True
    )


class Platform(TimestampMixin, Base):
    __tablename__ = "platforms"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[PlatformCategory] = mapped_column(
        pg_enum(PlatformCategory, "platform_category")
    )
    connector_type: Mapped[str | None] = mapped_column(String(60))
    implementation_status: Mapped[ImplementationStatus] = mapped_column(
        pg_enum(ImplementationStatus, "implementation_status"),
        default=ImplementationStatus.NOT_STARTED,
        server_default=ImplementationStatus.NOT_STARTED.value,
    )
    docs_url: Mapped[str | None] = mapped_column(Text)
    extraction_strategy: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True)
    website: Mapped[str | None] = mapped_column(Text)


class CompanyAlias(TimestampMixin, Base):
    __tablename__ = "company_aliases"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = fk("companies.id", "CASCADE")
    alias: Mapped[str] = mapped_column(String(300))
    normalized_alias: Mapped[str] = mapped_column(String(300), unique=True)


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("platform_id", "external_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    platform_id: Mapped[uuid.UUID] = fk("platforms.id", "RESTRICT")
    company_id: Mapped[uuid.UUID | None] = fk("companies.id", "SET NULL", nullable=True)
    external_id: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(Text)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    last_successful_fetch_at: Mapped[datetime | None] = tstz(nullable=True)


_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(location, '')), 'B') || "
    "setweight(to_tsvector('simple', coalesce(description, '')), 'C')"
)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_search_vector", "search_vector", postgresql_using="gin"),
        # O indice trigram em normalized_title (gin_trgm_ops) e criado so na migration,
        # pois depende da extensao pg_trgm estar disponivel no servidor.
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    canonical_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    company_id: Mapped[uuid.UUID] = fk("companies.id", "RESTRICT")
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(String(60))
    workplace_type: Mapped[str | None] = mapped_column(String(60))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    salary_currency: Mapped[str | None] = mapped_column(CHAR(3))
    original_posted_at: Mapped[datetime | None] = tstz(nullable=True)
    first_seen_at: Mapped[datetime] = tstz()
    last_seen_at: Mapped[datetime] = tstz()
    last_checked_at: Mapped[datetime] = tstz()
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        default=JobStatus.OPEN,
        server_default=JobStatus.OPEN.value,
        index=True,
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_SEARCH_VECTOR_SQL, persisted=True)
    )


class JobSource(TimestampMixin, Base):
    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("source_id", "external_job_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = fk("jobs.id", "CASCADE")
    source_id: Mapped[uuid.UUID] = fk("sources.id", "CASCADE")
    external_job_id: Mapped[str] = mapped_column(String(300))
    original_url: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = tstz()
    last_seen_at: Mapped[datetime] = tstz()
    last_checked_at: Mapped[datetime] = tstz()
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"),
        default=JobStatus.OPEN,
        server_default=JobStatus.OPEN.value,
    )
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class FetchRun(Base):
    __tablename__ = "fetch_runs"
    __table_args__ = (
        Index("ix_fetch_runs_source_id_started_at", "source_id", text("started_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = tstz()
    finished_at: Mapped[datetime | None] = tstz(nullable=True)
    status: Mapped[FetchRunStatus] = mapped_column(
        pg_enum(FetchRunStatus, "fetch_run_status"), default=FetchRunStatus.RUNNING
    )
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    jobs_inserted: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    jobs_unchanged: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    jobs_closed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    records_failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class JobHistory(Base):
    __tablename__ = "job_history"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = fk("jobs.id", "CASCADE")
    source_id: Mapped[uuid.UUID | None] = fk("sources.id", "SET NULL", nullable=True)
    fetch_run_id: Mapped[uuid.UUID | None] = fk("fetch_runs.id", "SET NULL", nullable=True)
    field_name: Mapped[str] = mapped_column(String(60))
    old_value: Mapped[Any] = mapped_column(JSONB)
    new_value: Mapped[Any] = mapped_column(JSONB)
    detected_at: Mapped[datetime] = tstz()


class DedupMatch(Base):
    """Usada a partir da Fase 2 (deduplicacao cross-source)."""

    __tablename__ = "dedup_matches"
    __table_args__ = (
        UniqueConstraint("job_id_a", "job_id_b"),
        CheckConstraint("job_id_a <> job_id_b", name="distinct_jobs"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id_a: Mapped[uuid.UUID] = fk("jobs.id", "CASCADE")
    job_id_b: Mapped[uuid.UUID] = fk("jobs.id", "CASCADE")
    score: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(60))
    decision: Mapped[DedupDecision] = mapped_column(pg_enum(DedupDecision, "dedup_decision"))
    created_at: Mapped[datetime] = tstz(server_default=text("now()"))


class ConnectorError(Base):
    __tablename__ = "connector_errors"

    id: Mapped[uuid.UUID] = uuid_pk()
    fetch_run_id: Mapped[uuid.UUID] = fk("fetch_runs.id", "CASCADE")
    external_job_id: Mapped[str | None] = mapped_column(String(300))
    error_type: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = tstz(server_default=text("now()"))
