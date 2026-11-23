"""Execucao do projeto dbt (silver + gold) sobre o bronze, sem Postgres.

Modelos com tag `history` (Hacker News) tem rotina propria (`scripts/build_history.py`); o fluxo
mensal os exclui com `--exclude tag:history` e as tabelas continuam no warehouse.

Os caminhos chegam ao dbt por variaveis de ambiente (ver analytics/profiles.yml); target e logs
do dbt ficam dentro do lake para nao sujar o repositorio.
"""

import gc
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dbt.adapters.duckdb.connections import DuckDBConnectionManager
from dbt.adapters.factory import reset_adapters
from dbt.cli.main import dbtRunner

DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "analytics"


class DbtError(RuntimeError):
    pass


@contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, prev in old.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


def _release_duckdb() -> None:
    """Fecha a conexao DuckDB que o dbt-duckdb mantem no processo (cache de classe)."""
    reset_adapters()  # type: ignore[no-untyped-call]
    env = getattr(DuckDBConnectionManager, "_ENV", None)
    if env is not None:
        env.close()
        DuckDBConnectionManager._ENV = None
    gc.collect()


def run_dbt(
    lake_dir: Path | str,
    project_dir: Path | str | None = None,
    command: str = "build",
    extra_args: list[str] | None = None,
) -> Path:
    """Roda `dbt <command>` e devolve o caminho do warehouse. Levanta DbtError se falhar."""
    lake = Path(lake_dir).resolve()
    project = Path(project_dir or DEFAULT_PROJECT_DIR).resolve()
    warehouse = lake / "warehouse.duckdb"
    work = lake / ".dbt"
    env = {
        "VAYA_WAREHOUSE": warehouse.as_posix(),
        "VAYA_BRONZE_JOBS": (lake / "bronze" / "jobs" / "*" / "*.parquet").as_posix(),
        "VAYA_BRONZE_RUNS": (lake / "bronze" / "runs" / "*" / "*.parquet").as_posix(),
        "VAYA_HN_POSTS": (lake / "history/hn/bronze/posts/*/*.parquet").as_posix(),
        "VAYA_HN_THREADS": (lake / "history/hn/bronze/threads/*/*.parquet").as_posix(),
    }
    args = [
        command,
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--target-path",
        str(work / "target"),
        "--log-path",
        str(work / "logs"),
        "--quiet",
        *(extra_args or []),
    ]
    lake.mkdir(parents=True, exist_ok=True)
    with _env(env):
        try:
            result = dbtRunner().invoke(args)
        finally:
            # Libera a conexao DuckDB em memoria do dbt, senao outras conexoes ao arquivo falham.
            _release_duckdb()
    if not result.success:
        detail = repr(result.exception) if result.exception else "ver a saida do dbt acima"
        raise DbtError(f"dbt {command} falhou: {detail}")
    return warehouse
