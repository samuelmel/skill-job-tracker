"""schema inicial

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

ENUMS = {
    "platform_category": ("ATS", "JOB_BOARD"),
    "implementation_status": (
        "NOT_STARTED",
        "RESEARCHING",
        "IMPLEMENTED",
        "TESTED",
        "VERIFIED",
        "BLOCKED",
    ),
    "job_status": ("OPEN", "CLOSED", "UNKNOWN"),
    "fetch_run_status": ("RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"),
    "dedup_decision": ("MERGED", "REVIEW", "REJECTED"),
}

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(location, '')), 'B') || "
    "setweight(to_tsvector('simple', coalesce(description, '')), 'C')"
)


def enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def uuid_pk() -> sa.Column[sa.Uuid]:
    return sa.Column(
        "id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def fk_col(name: str, target: str, ondelete: str, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        pg.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def tstz(name: str, nullable: bool = False, **kw: object) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)  # type: ignore[arg-type]


def int_col(name: str) -> sa.Column[int]:
    return sa.Column(name, sa.Integer, nullable=False, server_default=sa.text("0"))


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        enum(name).create(bind, checkfirst=True)

    op.create_table(
        "platforms",
        uuid_pk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("category", enum("platform_category"), nullable=False),
        sa.Column("connector_type", sa.String(60)),
        sa.Column(
            "implementation_status",
            enum("implementation_status"),
            nullable=False,
            server_default="NOT_STARTED",
        ),
        sa.Column("docs_url", sa.Text),
        sa.Column("extraction_strategy", sa.Text),
        sa.Column("notes", sa.Text),
        *timestamps(),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "companies",
        uuid_pk(),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("website", sa.Text),
        *timestamps(),
        sa.UniqueConstraint("normalized_name"),
    )

    op.create_table(
        "company_aliases",
        uuid_pk(),
        fk_col("company_id", "companies.id", "CASCADE"),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("normalized_alias", sa.String(300), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])

    op.create_table(
        "sources",
        uuid_pk(),
        fk_col("platform_id", "platforms.id", "RESTRICT"),
        fk_col("company_id", "companies.id", "SET NULL", nullable=True),
        sa.Column("external_id", sa.String(300), nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column(
            "configuration", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        tstz("last_successful_fetch_at", nullable=True),
        *timestamps(),
        sa.UniqueConstraint("platform_id", "external_id"),
    )
    op.create_index("ix_sources_platform_id", "sources", ["platform_id"])
    op.create_index("ix_sources_company_id", "sources", ["company_id"])

    op.create_table(
        "jobs",
        uuid_pk(),
        sa.Column("canonical_fingerprint", sa.String(64), nullable=False),
        fk_col("company_id", "companies.id", "RESTRICT"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("normalized_title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("location", sa.Text),
        sa.Column("employment_type", sa.String(60)),
        sa.Column("workplace_type", sa.String(60)),
        sa.Column("salary_min", sa.Numeric(14, 2)),
        sa.Column("salary_max", sa.Numeric(14, 2)),
        sa.Column("salary_currency", pg.CHAR(3)),
        tstz("original_posted_at", nullable=True),
        tstz("first_seen_at"),
        tstz("last_seen_at"),
        tstz("last_checked_at"),
        sa.Column("status", enum("job_status"), nullable=False, server_default="OPEN"),
        sa.Column("search_vector", pg.TSVECTOR, sa.Computed(SEARCH_VECTOR_SQL, persisted=True)),
        *timestamps(),
    )
    op.create_index("ix_jobs_canonical_fingerprint", "jobs", ["canonical_fingerprint"])
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_search_vector", "jobs", ["search_vector"], postgresql_using="gin")
    _create_trigram_index(bind)

    op.create_table(
        "job_sources",
        uuid_pk(),
        fk_col("job_id", "jobs.id", "CASCADE"),
        fk_col("source_id", "sources.id", "CASCADE"),
        sa.Column("external_job_id", sa.String(300), nullable=False),
        sa.Column("original_url", sa.Text),
        sa.Column("application_url", sa.Text),
        sa.Column("raw_data", pg.JSONB, nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        tstz("first_seen_at"),
        tstz("last_seen_at"),
        tstz("last_checked_at"),
        sa.Column("status", enum("job_status"), nullable=False, server_default="OPEN"),
        int_col("missing_count"),
        *timestamps(),
        sa.UniqueConstraint("source_id", "external_job_id"),
    )
    op.create_index("ix_job_sources_job_id", "job_sources", ["job_id"])
    op.create_index("ix_job_sources_source_id", "job_sources", ["source_id"])

    op.create_table(
        "fetch_runs",
        uuid_pk(),
        fk_col("source_id", "sources.id", "CASCADE"),
        tstz("started_at"),
        tstz("finished_at", nullable=True),
        sa.Column("status", enum("fetch_run_status"), nullable=False),
        int_col("jobs_found"),
        int_col("jobs_inserted"),
        int_col("jobs_updated"),
        int_col("jobs_unchanged"),
        int_col("jobs_closed"),
        int_col("records_failed"),
        sa.Column("error_message", sa.Text),
        sa.Column("duration_ms", sa.Integer),
    )
    op.create_index(
        "ix_fetch_runs_source_id_started_at", "fetch_runs", ["source_id", sa.text("started_at DESC")]
    )

    op.create_table(
        "job_history",
        uuid_pk(),
        fk_col("job_id", "jobs.id", "CASCADE"),
        fk_col("source_id", "sources.id", "SET NULL", nullable=True),
        fk_col("fetch_run_id", "fetch_runs.id", "SET NULL", nullable=True),
        sa.Column("field_name", sa.String(60), nullable=False),
        sa.Column("old_value", pg.JSONB),
        sa.Column("new_value", pg.JSONB),
        tstz("detected_at"),
    )
    op.create_index("ix_job_history_job_id", "job_history", ["job_id"])
    op.create_index("ix_job_history_source_id", "job_history", ["source_id"])
    op.create_index("ix_job_history_fetch_run_id", "job_history", ["fetch_run_id"])

    op.create_table(
        "dedup_matches",
        uuid_pk(),
        fk_col("job_id_a", "jobs.id", "CASCADE"),
        fk_col("job_id_b", "jobs.id", "CASCADE"),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("method", sa.String(60), nullable=False),
        sa.Column("decision", enum("dedup_decision"), nullable=False),
        tstz("created_at", server_default=sa.text("now()")),
        sa.UniqueConstraint("job_id_a", "job_id_b"),
        sa.CheckConstraint("job_id_a <> job_id_b", name="distinct_jobs"),
    )
    op.create_index("ix_dedup_matches_job_id_a", "dedup_matches", ["job_id_a"])
    op.create_index("ix_dedup_matches_job_id_b", "dedup_matches", ["job_id_b"])

    op.create_table(
        "connector_errors",
        uuid_pk(),
        fk_col("fetch_run_id", "fetch_runs.id", "CASCADE"),
        sa.Column("external_job_id", sa.String(300)),
        sa.Column("error_type", sa.String(120), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB),
        tstz("created_at", server_default=sa.text("now()")),
    )
    op.create_index("ix_connector_errors_fetch_run_id", "connector_errors", ["fetch_run_id"])


def _create_trigram_index(bind: sa.Connection) -> None:
    """Cria pg_trgm + indice trigram, se a extensao existir no servidor.

    Servidores minimos (ex.: pgserver embutido, usado nos testes locais) nao trazem
    contrib; nesse caso o indice e pulado com aviso e as demais estruturas funcionam.
    """
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).first()
    if available is None:
        log.warning("pg_trgm indisponivel neste servidor: indice trigram ignorado")
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_jobs_normalized_title_trgm ON jobs USING gin (normalized_title gin_trgm_ops)"
    )


def downgrade() -> None:
    for table in (
        "connector_errors",
        "dedup_matches",
        "job_history",
        "fetch_runs",
        "job_sources",
        "jobs",
        "sources",
        "company_aliases",
        "companies",
        "platforms",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for name in ENUMS:
        enum(name).drop(bind, checkfirst=True)
