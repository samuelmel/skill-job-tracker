"""identidade e processamento: salary_period, URLs canonicas e dedup_matches estendida

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERIODS = "('HOUR', 'DAY', 'WEEK', 'MONTH', 'YEAR')"


def upgrade() -> None:
    # Periodo salarial: sem ele, valores por hora e por ano se misturariam nos filtros.
    op.add_column("jobs", sa.Column("salary_period", sa.String(10)))
    op.create_check_constraint(op.f("ck_jobs_salary_period"), "jobs", f"salary_period IN {PERIODS}")
    op.create_index("ix_jobs_company_id_normalized_title", "jobs", ["company_id", "normalized_title"])

    op.add_column("job_sources", sa.Column("canonical_url", sa.Text))
    op.add_column("job_sources", sa.Column("canonical_application_url", sa.Text))
    op.create_index("ix_job_sources_canonical_url", "job_sources", ["canonical_url"])
    op.create_index(
        "ix_job_sources_canonical_application_url", "job_sources", ["canonical_application_url"]
    )
    _backfill_canonical_urls()

    op.add_column(
        "dedup_matches",
        sa.Column("reasons", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "dedup_matches",
        sa.Column(
            "job_source_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("job_sources.id", ondelete="CASCADE"),
        ),
    )
    op.create_index("ix_dedup_matches_job_source_id", "dedup_matches", ["job_source_id"])
    op.alter_column("dedup_matches", "job_id_b", nullable=True)
    op.drop_constraint(op.f("ck_dedup_matches_distinct_jobs"), "dedup_matches", type_="check")
    op.create_check_constraint(
        op.f("ck_dedup_matches_distinct_jobs"),
        "dedup_matches",
        "job_id_b IS NULL OR job_id_a <> job_id_b",
    )
    op.create_index(
        "uq_dedup_matches_job_id_a_job_source_id",
        "dedup_matches",
        ["job_id_a", "job_source_id"],
        unique=True,
        postgresql_where=sa.text("job_source_id IS NOT NULL"),
    )


_TRACKING_PARAMS = frozenset({"gh_src", "source", "ref", "fbclid", "gclid", "lever-source"})
_BATCH = 1000


def _canonical_url(url: str) -> str:
    """Copia CONGELADA de processing.urls.canonical_url na epoca desta migration."""
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS)
        )
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _backfill_canonical_urls() -> None:
    """Backfill em lotes por id (keyset), sem carregar a tabela inteira."""
    bind = op.get_bind()
    last_id = None
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT id, original_url, application_url FROM job_sources "
                "WHERE (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid)) "
                "ORDER BY id LIMIT :n"
            ),
            {"last": last_id, "n": _BATCH},
        ).all()
        if not rows:
            return
        for row_id, original, application in rows:
            bind.execute(
                sa.text(
                    "UPDATE job_sources SET canonical_url = :c, canonical_application_url = :a "
                    "WHERE id = :id"
                ),
                {
                    "c": _canonical_url(original) if original else None,
                    "a": _canonical_url(application) if application else None,
                    "id": row_id,
                },
            )
        last_id = str(rows[-1][0])


def downgrade() -> None:
    # Linhas MERGED (job_id_b NULL) nao existem no schema 0001 e sao descartadas (dado derivado).
    op.drop_index("uq_dedup_matches_job_id_a_job_source_id", table_name="dedup_matches")
    op.execute("DELETE FROM dedup_matches WHERE job_id_b IS NULL")
    op.drop_constraint(op.f("ck_dedup_matches_distinct_jobs"), "dedup_matches", type_="check")
    op.create_check_constraint(
        op.f("ck_dedup_matches_distinct_jobs"), "dedup_matches", "job_id_a <> job_id_b"
    )
    op.alter_column("dedup_matches", "job_id_b", nullable=False)
    op.drop_index("ix_dedup_matches_job_source_id", table_name="dedup_matches")
    op.drop_column("dedup_matches", "job_source_id")
    op.drop_column("dedup_matches", "reasons")

    op.drop_index("ix_job_sources_canonical_application_url", table_name="job_sources")
    op.drop_index("ix_job_sources_canonical_url", table_name="job_sources")
    op.drop_column("job_sources", "canonical_application_url")
    op.drop_column("job_sources", "canonical_url")

    op.drop_index("ix_jobs_company_id_normalized_title", table_name="jobs")
    op.drop_constraint(op.f("ck_jobs_salary_period"), "jobs", type_="check")
    op.drop_column("jobs", "salary_period")
