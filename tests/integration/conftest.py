"""Postgres real: TEST_DATABASE_URL, ou um servidor embutido via pgserver (so dev)."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vaya_indexer.database.repositories.platforms import PlatformRepository
from vaya_indexer.database.session import create_engine, create_session_factory
from vaya_indexer.platforms_catalog import build_catalog

ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.nodeid.replace("\\", "/").split("/"):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        yield external
        return
    import pgserver

    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="delete")
    uri = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    yield uri.render_as_string(hide_password=False)
    server.cleanup()


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(migrated_url)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE connector_errors, job_history, dedup_matches, fetch_runs, job_sources, "
                "jobs, sources, company_aliases, companies, platforms CASCADE"
            )
        )
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def seeded(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await PlatformRepository(session).upsert_catalog(build_catalog())
        await session.commit()
