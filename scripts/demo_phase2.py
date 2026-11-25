"""Demo reproduzivel da Fase 2 (identidade, dedup e reconciliacao) contra Postgres real.

Uso: uv run python scripts/demo_phase2.py

ATENCAO: os dados abaixo sao DADOS DE DEMONSTRACAO CONTROLADOS, servidos por um servidor HTTP
local em formato Greenhouse. Nao sao dados reais nem evidencia de cobertura; a cobertura real
vem dos testes `live`. O banco e um Postgres embutido (pgserver) em ./.pgdata-demo2, e o schema
public e recriado a cada execucao (estado limpo).
"""

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pgserver
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from skilometer.core.config import ProcessingPolicy
from skilometer.database.models import DedupMatch, Job, JobHistory, JobSource
from skilometer.database.repositories.platforms import PlatformRepository
from skilometer.database.session import create_engine, create_session_factory
from skilometer.extraction.http import HttpClient
from skilometer.ingestion.service import FetchRunSummary, IngestionService, register_source
from skilometer.platforms_catalog import build_catalog

ROOT = Path(__file__).resolve().parents[1]
COMPANY = "Acme Demo"
BOARD_1, BOARD_2 = "acme-demo", "acme-demo-careers"
LOREM = " ".join(f"termo{i}" for i in range(60))

STATE: dict[str, list[dict[str, Any]]] = {}


def job(job_id: int, title: str, location: str, board: str, description: str = LOREM) -> dict:
    return {
        "id": job_id,
        "title": title,
        "updated_at": "2025-01-01T00:00:00Z",
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/{board}/jobs/{job_id}",
        "company_name": COMPANY,
        "metadata": None,
        "content": f"&lt;p&gt;{description}&lt;/p&gt;",
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        board = self.path.split("/v1/boards/")[1].split("/")[0]
        jobs = STATE.get(board, [])
        body = json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return None


def _short(value: object, limit: int = 40) -> str:
    text_ = repr(value)
    return text_ if len(text_) <= limit else text_[: limit - 3] + "..."


def metrics(label: str, s: FetchRunSummary) -> None:
    print(
        f"{label:<44} status={s.status.value:<9} found={s.jobs_found} inserted={s.jobs_inserted} "
        f"updated={s.jobs_updated} unchanged={s.jobs_unchanged} closed={s.jobs_closed}"
        + (f" msg={s.error_message!r}" if s.error_message else "")
    )


async def scenario(url: str, port: int) -> None:
    engine = create_engine(url)
    factory = create_session_factory(engine)
    base = f"http://127.0.0.1:{port}/v1/boards"
    async with factory() as session:
        await PlatformRepository(session).upsert_catalog(build_catalog())
        await session.commit()
    ids = {}
    for board in (BOARD_1, BOARD_2):
        async with factory() as session:
            src = await register_source(
                session,
                platform_slug="greenhouse",
                company_name=COMPANY,
                config={"board_token": board, "api_base_url": base},
            )
            await session.commit()
            ids[board] = src.id

    base_jobs = [
        job(1, "Senior Backend Engineer", "Berlin, Germany", BOARD_1),
        job(2, "Data Analyst", "Lisbon, Portugal", BOARD_1),
        job(3, "Product Designer", "Remote - US", BOARD_1),
        job(4, "Support Specialist", "Austin, TX, United States", BOARD_1),
    ]
    STATE[BOARD_1] = copy.deepcopy(base_jobs)
    policy = ProcessingPolicy(reconcile_grace_hours=0)  # demo: sem janela de graca
    async with HttpClient(user_agent="skilometer-demo", max_retries=1) as http:
        service = IngestionService(factory, http, policy)
        run = service.run_source
        metrics("coleta 1 (inicial)", await run(ids[BOARD_1]))
        metrics("coleta 2 (identica, idempotente)", await run(ids[BOARD_1]))

        STATE[BOARD_1][1]["title"] = "Senior Data Analyst"
        STATE[BOARD_1][1]["content"] = f"&lt;p&gt;{LOREM} extra&lt;/p&gt;"
        metrics("coleta 3 (titulo e descricao mudaram)", await run(ids[BOARD_1]))

        STATE[BOARD_1] = [j for j in STATE[BOARD_1] if j["id"] != 4]
        metrics("coleta 4 (vaga 4 ausente 1a vez)", await run(ids[BOARD_1]))
        async with factory() as session:
            js = await session.scalar(select(JobSource).where(JobSource.external_job_id == "4"))
            assert js is not None
            print(f"   -> vaga 4: status={js.status.value} missing_count={js.missing_count}")
        metrics("coleta 5 (vaga 4 ausente 2a vez)", await run(ids[BOARD_1]))

        STATE[BOARD_2] = [job(500, "Senior Backend Engineer", "Berlin, Germany", BOARD_2)]
        metrics("coleta 6 (2a fonte, vaga equivalente)", await run(ids[BOARD_2]))

    async with factory() as session:
        print("\n--- resultado no banco ---")
        print("jobs:", await session.scalar(select(func.count()).select_from(Job)))
        print("job_sources:", await session.scalar(select(func.count()).select_from(JobSource)))
        for j in await session.scalars(select(Job).order_by(Job.title)):
            print(f"  job {j.title!r:<28} status={j.status.value}")
        for h in await session.scalars(
            select(JobHistory).where(JobHistory.field_name != "source_status")
        ):
            print(f"  history {h.field_name}: {_short(h.old_value)} -> {_short(h.new_value)}")
        for m in await session.scalars(select(DedupMatch)):
            print(
                f"  dedup {m.decision.value} score={m.score:.2f} method={m.method} "
                f"reasons={m.reasons}"
            )
    await engine.dispose()


def main() -> None:
    server = pgserver.get_server(ROOT / ".pgdata-demo2", cleanup_mode="stop")
    url = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
    rendered = url.render_as_string(hide_password=False)
    sync = create_sync_engine(url, isolation_level="AUTOCOMMIT")
    with sync.connect() as conn:  # estado limpo e reproduzivel
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    sync.dispose()
    env = {**os.environ, "DATABASE_URL": rendered, "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("DADOS DE DEMONSTRACAO CONTROLADOS (servidor local); nao e evidencia de cobertura.\n")
    try:
        asyncio.run(
            scenario(rendered, httpd.server_address[1]), loop_factory=asyncio.SelectorEventLoop
        )
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
