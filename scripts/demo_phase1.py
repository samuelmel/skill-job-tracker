"""Demo reproduzivel da Fase 1 contra um Postgres real (embutido via pgserver).

Uso: uv run python scripts/demo_phase1.py [board_token]

Sobe um Postgres em ./.pgdata (ignorado no git), aplica as migrations, carrega o
catalogo, cadastra um board real do Greenhouse e executa a ingestao duas vezes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pgserver
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ("gitlab", "airbnb", "discord", "stripe")


def skilometer(*args: str, env: dict[str, str], check: bool = True) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "skilometer", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"skilometer {' '.join(args)} falhou:\n{result.stderr}\n{result.stdout}")
    return result.stdout


def summarize(label: str, raw: str) -> None:
    data = json.loads(raw)
    keys = (
        "status", "jobs_found", "jobs_inserted", "jobs_updated", "jobs_unchanged",
        "jobs_closed", "records_failed", "duration_ms",
    )  # fmt: skip
    print(f"{label}: " + " ".join(f"{k}={data[k]}" for k in keys))


def main() -> None:
    boards = (sys.argv[1],) if len(sys.argv) > 1 else BOARDS
    server = pgserver.get_server(ROOT / ".pgdata", cleanup_mode="stop")
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
    print(skilometer("seed-platforms", env=env).strip())
    for board in boards:
        out = skilometer(
            "add-source", "--platform", "greenhouse", "--company", board.title(),
            "--config", json.dumps({"board_token": board}), env=env,
        )  # fmt: skip
        source_id = out.split("source_id=")[1].split()[0]
        first = skilometer("fetch", "--source-id", source_id, env=env, check=False)
        if not first.strip() or json.loads(first)["jobs_found"] == 0:
            continue
        print(f"board={board} source_id={source_id}")
        summarize("run 1", first)
        summarize("run 2", skilometer("fetch", "--source-id", source_id, env=env))
        print(skilometer("list-jobs", "--limit", "5", env=env).strip())
        return
    raise SystemExit("nenhum board respondeu com vagas")


if __name__ == "__main__":
    main()
