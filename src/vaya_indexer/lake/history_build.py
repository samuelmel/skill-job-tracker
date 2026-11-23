"""Bronze do historico do Hacker News: raw JSONL -> Parquet tipado, 100% offline.

Le `<lake>/history/hn/raw/month=*/thread=*/{posts.jsonl.gz,manifest.json}` (gravado pelo loader
`scripts/load_history_hn.py`) e grava, por thread:

- `<lake>/history/hn/bronze/posts/month=YYYY-MM/thread=<id>.parquet` (um post por linha);
- `<lake>/history/hn/bronze/threads/month=YYYY-MM/thread=<id>.parquet` (uma linha por thread,
  com os contadores do manifest e o sha256 do raw), escrito por ultimo.

A thread so conta como processada quando o arquivo de threads existe e traz o mesmo sha256 do
manifest atual; thread cujo raw mudou (mes mais novo, ainda crescendo) e refeita. Cada arquivo
sai num temporario irmao e e publicado com `os.replace`. Este modulo nao importa o loader (que
faz rede) de proposito: o fluxo mensal nunca toca o historico.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

POSTS_FILE = "posts.jsonl.gz"
MANIFEST_FILE = "manifest.json"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

POSTS_SCHEMA = pa.schema(
    [
        ("post_id", pa.int64()),
        ("thread_id", pa.int64()),
        ("month", pa.string()),
        ("created_at", pa.timestamp("us")),  # UTC, sem fuso
        ("text", pa.string()),
    ]
)
THREADS_SCHEMA = pa.schema(
    [
        ("thread_id", pa.int64()),
        ("month", pa.string()),
        ("title", pa.string()),
        ("thread_created_at", pa.timestamp("us")),
        ("fetched_at", pa.timestamp("us")),
        ("post_count", pa.int64()),
        ("rows_bronze", pa.int64()),
        ("api_top_level_count", pa.int64()),
        ("firebase_kids_count", pa.int64()),
        ("excluded_deleted_or_empty", pa.int64()),
        ("complete", pa.bool_()),
        ("source_sha256", pa.string()),
    ]
)


@dataclass
class BadThread:
    path: str
    reason: str


@dataclass
class HistoryBronzeResult:
    processed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    bad: list[BadThread] = field(default_factory=list)
    rows: int = 0


def history_root(lake_dir: Path) -> Path:
    return lake_dir / "history" / "hn"


def bronze_paths(lake_dir: Path, month: str, thread_id: int) -> tuple[Path, Path]:
    name = f"thread={thread_id}.parquet"
    base = history_root(lake_dir) / "bronze"
    return base / "posts" / f"month={month}" / name, base / "threads" / f"month={month}" / name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _naive_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is None else parsed.astimezone(UTC).replace(tzinfo=None)


def _write_parquet(table: pa.Table, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".tmp-{uuid.uuid4().hex}.parquet"
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _already_built(threads_path: Path, posts_path: Path, sha: str) -> bool:
    if not (threads_path.is_file() and posts_path.is_file()):
        return False
    try:
        rows = pq.read_table(threads_path, columns=["source_sha256"]).column(0).to_pylist()
    except Exception:  # arquivo corrompido: refaz
        return False
    return bool(rows == [sha])


def iter_manifests(lake_dir: Path) -> list[Path]:
    found = sorted(history_root(lake_dir).glob(f"raw/month=*/thread=*/{MANIFEST_FILE}"))
    return [p for p in found if not any(part.startswith((".tmp-", ".old-")) for part in p.parts)]


def _process_thread(manifest_path: Path, lake_dir: Path, result: HistoryBronzeResult) -> None:
    thread_dir = manifest_path.parent
    try:
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        thread_id = int(manifest["thread_id"])
        month = str(manifest["month"])
        expected_sha = str(manifest["sha256"])
        if not _MONTH.match(month):
            raise ValueError(f"mes invalido '{month}'")
    except Exception as exc:
        result.bad.append(BadThread(str(thread_dir), f"manifest invalido: {exc!r}"[:300]))
        return
    posts_file = thread_dir / str(manifest.get("posts_file", POSTS_FILE))
    if not posts_file.is_file():
        result.bad.append(BadThread(str(thread_dir), f"arquivo ausente: {posts_file.name}"))
        return
    if _sha256(posts_file) != expected_sha:
        result.bad.append(
            BadThread(str(thread_dir), f"sha256 divergente (manifest {expected_sha[:12]}...)")
        )
        return
    posts_out, threads_out = bronze_paths(lake_dir, month, thread_id)
    if _already_built(threads_out, posts_out, expected_sha):
        result.skipped.append(str(thread_id))
        return

    rows: list[dict[str, Any]] = []
    try:
        with gzip.open(posts_file, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                rows.append(
                    {
                        "post_id": int(rec["post_id"]),
                        "thread_id": int(rec["thread_id"]),
                        "month": str(rec["month"]),
                        "created_at": _naive_utc(str(rec["created_at"])),
                        "text": str(rec["text"]),
                    }
                )
        thread_row = {
            "thread_id": thread_id,
            "month": month,
            "title": manifest.get("title"),
            "thread_created_at": _naive_utc(str(manifest["thread_created_at"])),
            "fetched_at": _naive_utc(str(manifest["fetched_at"])),
            "post_count": int(manifest["post_count"]),
            "rows_bronze": len(rows),
            "api_top_level_count": manifest.get("api_top_level_count"),
            "firebase_kids_count": manifest.get("firebase_kids_count"),
            "excluded_deleted_or_empty": manifest.get("excluded_deleted_or_empty"),
            "complete": bool(manifest.get("complete")),
            "source_sha256": expected_sha,
        }
    except Exception as exc:
        result.bad.append(BadThread(str(thread_dir), f"conteudo invalido: {exc!r}"[:300]))
        return
    if len(rows) != thread_row["post_count"]:
        result.bad.append(
            BadThread(
                str(thread_dir), f"manifest diz {thread_row['post_count']} posts, ha {len(rows)}"
            )
        )
        return

    _write_parquet(pa.Table.from_pylist(rows, schema=POSTS_SCHEMA), posts_out)
    # Por ultimo: so depois disto a thread conta como processada.
    _write_parquet(pa.Table.from_pylist([thread_row], schema=THREADS_SCHEMA), threads_out)
    result.processed.append(str(thread_id))
    result.rows += len(rows)


def build_history_bronze(lake_dir: Path | str) -> HistoryBronzeResult:
    """Processa as threads novas ou alteradas do raw. Idempotente."""
    lake = Path(lake_dir)
    result = HistoryBronzeResult()
    for manifest_path in iter_manifests(lake):
        _process_thread(manifest_path, lake, result)
    for bad in result.bad:
        log.warning("raw do historico ignorado: %s (%s)", bad.path, bad.reason)
    return result


# cautious: so roda testes cujos pais estao todos selecionados (nao os de silver_job_skill).
HISTORY_DBT_ARGS = ["--select", "skills", "tag:history", "--indirect-selection", "cautious"]


def run_history_build(lake_dir: Path | str, project_dir: Path | str | None = None) -> Path:
    """Bronze do historico e depois so os modelos dbt com tag `history`; devolve o warehouse.

    Levanta RuntimeError se algum raw for rejeitado (sha256 divergente, manifest invalido...):
    uma serie historica com mes faltando em silencio seria enganosa.
    """
    from vaya_indexer.lake.transform import run_dbt

    lake = Path(lake_dir)
    result = build_history_bronze(lake)
    if result.bad:
        detail = "; ".join(f"{b.path}: {b.reason}" for b in result.bad)
        raise RuntimeError(f"{len(result.bad)} thread(s) do raw rejeitada(s): {detail}")
    if not (history_root(lake) / "bronze" / "threads").is_dir():
        raise RuntimeError(f"nenhuma thread em {history_root(lake) / 'raw'}: nada para construir")
    return run_dbt(lake, project_dir, extra_args=HISTORY_DBT_ARGS)
