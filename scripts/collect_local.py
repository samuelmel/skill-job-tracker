"""Coleta local sem Docker: Postgres embutido (pgserver) + migrations + catalogo + fetch-all.

Uso: uv run python scripts/collect_local.py --data-dir .pgdata-lake [--lake-dir data]
         [--limit N] [--platform slug]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pgserver
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=".pgdata-lake")
    parser.add_argument("--lake-dir", default="data")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--platform")
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()

    server = pgserver.get_server(ROOT / args.data_dir, cleanup_mode="stop")
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
    fetch = ["fetch-all", "--lake-dir", str((ROOT / args.lake_dir).resolve())]
    if args.limit:
        fetch += ["--limit", str(args.limit)]
    if args.platform:
        fetch += ["--platform", args.platform]
    if args.concurrency:
        fetch += ["--concurrency", str(args.concurrency)]
    return vaya(*fetch, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
