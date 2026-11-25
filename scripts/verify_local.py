"""Roda `skilometer verify` contra um Postgres embutido (pgserver), sem Docker.

Uso: uv run python scripts/verify_local.py --data-dir .pgdata-verify-a ashby lever-co
Sem slugs, verifica todas as plataformas (`verify --all`).
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
    parser.add_argument("--data-dir", default=".pgdata-verify")
    parser.add_argument("--report", action="store_true", help="regenera docs/coverage.*")
    parser.add_argument("slugs", nargs="*")
    args = parser.parse_args()

    server = pgserver.get_server(ROOT / args.data_dir, cleanup_mode="stop")
    url = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "LOG_LEVEL": "WARNING",
        "PYTHONIOENCODING": "utf-8",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    # idempotente: nunca rebaixa IMPLEMENTED/TESTED/VERIFIED
    subprocess.run(
        [sys.executable, "-m", "skilometer", "seed-platforms"], cwd=ROOT, env=env, check=True
    )
    targets = [["--platform", s] for s in args.slugs] or [["--all"]]
    code = 0
    for target in targets:
        result = subprocess.run(
            [sys.executable, "-m", "skilometer", "verify", *target],
            cwd=ROOT,
            env=env,
            check=False,
        )
        code = code or result.returncode
    if args.report:
        subprocess.run(
            [sys.executable, "-m", "skilometer", "coverage-report"], cwd=ROOT, env=env, check=True
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
