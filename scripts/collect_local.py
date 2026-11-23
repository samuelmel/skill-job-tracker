"""Coleta local sem Docker: Postgres embutido (pgserver) + migrations + catalogo + fetch-all.

Uso: uv run python scripts/collect_local.py --data-dir .pgdata-lake [--lake-dir data]
         [--limit N] [--platform slug]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pgserver
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent


def collect(
    data_dir: str,
    lake_dir: str,
    *,
    limit: int | None = None,
    platform: str | None = None,
    concurrency: int | None = None,
) -> tuple[int, dict[str, int] | None]:
    """Postgres embutido + migrations + seed + catalogo + fetch-all.

    Devolve (codigo de saida do fetch-all, contagens). As contagens sao None quando o
    fetch-all nao chegou a gravar o resumo.
    """
    server = pgserver.get_server(ROOT / data_dir, cleanup_mode="stop")
    url = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "LOG_LEVEL": "WARNING",
        "PYTHONIOENCODING": "utf-8",
    }

    def vaya(*cmd: str, check: bool = True) -> int:
        full = [sys.executable, "-m", "vaya_indexer", *cmd]
        return subprocess.run(full, cwd=ROOT, env=env, check=check).returncode

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    vaya("seed-platforms")
    vaya("load-catalog")
    with tempfile.TemporaryDirectory() as tmp:
        summary_path = Path(tmp) / "fetch_summary.json"
        fetch = [
            "fetch-all",
            "--lake-dir",
            str((ROOT / lake_dir).resolve()),
            "--summary-json",
            str(summary_path),
        ]
        if limit:
            fetch += ["--limit", str(limit)]
        if platform:
            fetch += ["--platform", platform]
        if concurrency:
            fetch += ["--concurrency", str(concurrency)]
        code = vaya(*fetch, check=False)
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
        )
    return code, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=".pgdata-lake")
    parser.add_argument("--lake-dir", default="data")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--platform")
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()
    code, _ = collect(
        args.data_dir,
        args.lake_dir,
        limit=args.limit,
        platform=args.platform,
        concurrency=args.concurrency,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
