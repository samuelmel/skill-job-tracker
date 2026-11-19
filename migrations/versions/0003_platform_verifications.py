"""evidencias de cobertura: platform_verifications e last_verified_* em platforms

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("platforms", sa.Column("last_verified_at", sa.DateTime(timezone=True)))
    op.add_column("platforms", sa.Column("last_verified_job_count", sa.Integer))
    op.create_table(
        "platform_verifications",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "platform_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("platforms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id", pg.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="SET NULL")
        ),
        sa.Column(
            "fetch_run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("fetch_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "verified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("jobs_extracted", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column(
            "sample_job_titles", pg.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("sample_urls", pg.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("connector_version", sa.String(60)),
    )
    op.create_index(
        "ix_platform_verifications_platform_id_verified_at",
        "platform_verifications",
        ["platform_id", "verified_at"],
    )
    op.create_index("ix_platform_verifications_source_id", "platform_verifications", ["source_id"])
    op.create_index(
        "ix_platform_verifications_fetch_run_id", "platform_verifications", ["fetch_run_id"]
    )


def downgrade() -> None:
    op.drop_table("platform_verifications")
    op.drop_column("platforms", "last_verified_job_count")
    op.drop_column("platforms", "last_verified_at")
