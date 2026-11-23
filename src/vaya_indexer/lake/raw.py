"""Escrita imutavel do raw: um diretorio por run, com jobs.jsonl.gz e manifest.json.

Layout: <lake_dir>/raw/platform=<slug>/source=<source>/dt=YYYY-MM-DD/run_id=<run>/
O diretorio da run e montado num diretorio temporario irmao e publicado com um unico rename,
entao leitores nunca veem uma run pela metade e uma run existente nunca e sobrescrita.
"""

import gzip
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from vaya_indexer.connectors.base import RawJob

SCHEMA_VERSION = 1
JOBS_FILE = "jobs.jsonl.gz"
MANIFEST_FILE = "manifest.json"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]|\.{2,}")
_MAX_PART = 120


def sanitize_part(value: str) -> str:
    """Torna um valor seguro como nome de diretorio (sem separadores nem `..`).

    Se a limpeza alterou o valor, anexa um hash curto do original para que valores diferentes
    nao colidam no mesmo diretorio.
    """
    clean = _UNSAFE.sub("_", value).lstrip(".")[:_MAX_PART]
    if clean == value and clean:
        return clean
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{clean or '_'}-{digest}"


class RawManifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    platform: str
    source_external_id: str
    source_id: str
    run_id: str
    fetched_at: str
    complete: bool
    job_count: int
    jobs_file: str = JOBS_FILE
    jobs_sha256: str
    connector: str


class RawWriteResult(BaseModel):
    path: str
    job_count: int
    jobs_sha256: str


class RawWriter(Protocol):
    """Contrato usado pela ingestao; `RawLakeWriter` e a implementacao em disco."""

    def write(
        self,
        *,
        platform: str,
        source_external_id: str,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        fetched_at: datetime,
        complete: bool,
        jobs: Sequence[RawJob],
        connector: str,
    ) -> RawWriteResult: ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _line(job: RawJob) -> bytes:
    record: dict[str, Any] = {"external_id": job.external_id, "payload": job.payload}
    return (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")


class RawLakeWriter:
    def __init__(self, lake_dir: Path | str) -> None:
        self.lake_dir = Path(lake_dir)

    def run_dir(
        self, platform: str, source_external_id: str, fetched_at: datetime, run_id: uuid.UUID
    ) -> Path:
        day = fetched_at.astimezone(UTC).strftime("%Y-%m-%d")
        return (
            self.lake_dir
            / "raw"
            / f"platform={sanitize_part(platform)}"
            / f"source={sanitize_part(source_external_id)}"
            / f"dt={day}"
            / f"run_id={sanitize_part(str(run_id))}"
        )

    def write(
        self,
        *,
        platform: str,
        source_external_id: str,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        fetched_at: datetime,
        complete: bool,
        jobs: Sequence[RawJob],
        connector: str,
    ) -> RawWriteResult:
        final = self.run_dir(platform, source_external_id, fetched_at, run_id)
        if final.exists():
            raise FileExistsError(f"raw ja existe, nao sera sobrescrito: {final}")
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.parent / f".tmp-{uuid.uuid4().hex}"
        tmp.mkdir()
        try:
            jobs_path = tmp / JOBS_FILE
            with (
                jobs_path.open("wb") as raw_fh,
                gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as gz,
            ):
                for job in jobs:
                    gz.write(_line(job))
            checksum = _sha256_file(jobs_path)
            manifest = RawManifest(
                platform=platform,
                source_external_id=source_external_id,
                source_id=str(source_id),
                run_id=str(run_id),
                fetched_at=fetched_at.astimezone(UTC).isoformat(),
                complete=complete,
                job_count=len(jobs),
                jobs_sha256=checksum,
                connector=connector,
            )
            (tmp / MANIFEST_FILE).write_text(
                manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            if final.exists():  # corrida entre dois escritores da mesma run
                raise FileExistsError(f"raw ja existe, nao sera sobrescrito: {final}")
            os.rename(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return RawWriteResult(path=str(final), job_count=len(jobs), jobs_sha256=checksum)
