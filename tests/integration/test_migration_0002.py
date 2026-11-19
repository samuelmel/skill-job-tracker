"""Migration 0002: upgrade a partir de um banco em 0001 e downgrade 0002 -> 0001."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "vaya_migration_test"


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def scratch_url(database_url: str) -> str:
    base = make_url(database_url)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
    admin.dispose()
    return base.set(database=DB_NAME).render_as_string(hide_password=False)


def _columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_upgrade_from_0001_and_downgrade_back(scratch_url: str) -> None:
    cfg = _cfg(scratch_url)
    command.upgrade(cfg, "0001")
    assert "salary_period" not in _columns(scratch_url, "jobs")
    assert "job_source_id" not in _columns(scratch_url, "dedup_matches")

    command.upgrade(cfg, "head")
    assert "salary_period" in _columns(scratch_url, "jobs")
    assert {"canonical_url", "canonical_application_url"} <= _columns(scratch_url, "job_sources")
    assert {"job_source_id", "reasons"} <= _columns(scratch_url, "dedup_matches")

    command.downgrade(cfg, "0001")
    assert "salary_period" not in _columns(scratch_url, "jobs")
    assert "canonical_url" not in _columns(scratch_url, "job_sources")
    assert "job_source_id" not in _columns(scratch_url, "dedup_matches")

    command.upgrade(cfg, "head")  # reaplicavel apos downgrade
    assert "salary_period" in _columns(scratch_url, "jobs")
