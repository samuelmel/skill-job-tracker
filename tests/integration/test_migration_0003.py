"""Migration 0003: upgrade a partir de 0002 e downgrade 0003 -> 0002."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "vaya_migration_test_0003"


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


def _schema(url: str) -> tuple[set[str], set[str]]:
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        columns = {c["name"] for c in insp.get_columns("platforms")}
        return tables, columns
    finally:
        engine.dispose()


def test_upgrade_from_0002_and_downgrade_back(scratch_url: str) -> None:
    cfg = _cfg(scratch_url)
    command.upgrade(cfg, "0002")
    tables, columns = _schema(scratch_url)
    assert "platform_verifications" not in tables
    assert "last_verified_at" not in columns

    command.upgrade(cfg, "head")
    tables, columns = _schema(scratch_url)
    assert "platform_verifications" in tables
    assert {"last_verified_at", "last_verified_job_count"} <= columns
    engine = create_engine(scratch_url)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("platform_verifications")}
    finally:
        engine.dispose()
    assert {
        "id",
        "platform_id",
        "source_id",
        "fetch_run_id",
        "verified_at",
        "jobs_extracted",
        "sample_job_titles",
        "sample_urls",
        "success",
        "error_message",
        "connector_version",
    } <= cols

    command.downgrade(cfg, "0002")
    tables, columns = _schema(scratch_url)
    assert "platform_verifications" not in tables
    assert "last_verified_at" not in columns

    command.upgrade(cfg, "head")  # reaplicavel
    assert "platform_verifications" in _schema(scratch_url)[0]
