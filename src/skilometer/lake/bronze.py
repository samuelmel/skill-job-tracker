"""Camada bronze: raw JSONL -> Parquet tipado, 100% offline.

Para cada run do raw ainda nao processada: confere o sha256 do manifest, roda o `normalize()`
do conector em cada linha e grava
`<lake>/bronze/jobs/month=YYYY-MM/run_id=<id>.parquet` (uma linha por vaga) e
`<lake>/bronze/runs/month=YYYY-MM/run_id=<id>.parquet` (uma linha por run, mesmo sem vagas).
Linha que falha no normalize continua no Parquet com `normalize_error`; nada e descartado.
Cada arquivo e escrito num temporario irmao e publicado com `os.replace` (atomico).
A run so conta como processada quando o arquivo de runs existe (e escrito por ultimo).
"""

import gzip
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from skilometer.connectors.base import BaseConnector, NormalizedJob, RawJob
from skilometer.connectors.registry import get_connector_class
from skilometer.core.exceptions import ConnectorNotFound, safe_error
from skilometer.extraction.http import HttpClient
from skilometer.lake.raw import MANIFEST_FILE, RawManifest, _sha256_file
from skilometer.source_catalog import DEFAULT_CATALOG_PATH, read_catalog

log = logging.getLogger(__name__)

JOBS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("source_external_id", pa.string()),
        ("platform", pa.string()),
        ("fetched_at", pa.timestamp("us")),  # UTC, sem fuso
        ("fetched_month", pa.string()),
        ("complete", pa.bool_()),
        ("external_id", pa.string()),
        ("title", pa.string()),
        ("company_name", pa.string()),
        ("description", pa.string()),
        ("location", pa.string()),
        ("original_url", pa.string()),
        ("application_url", pa.string()),
        ("salary_min", pa.float64()),
        ("salary_max", pa.float64()),
        ("salary_currency", pa.string()),
        ("salary_period", pa.string()),
        ("employment_type", pa.string()),
        ("workplace_type", pa.string()),
        ("original_posted_at", pa.timestamp("us")),
        ("payload_json", pa.string()),
        ("normalize_error", pa.string()),
    ]
)
RUNS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("source_external_id", pa.string()),
        ("platform", pa.string()),
        ("fetched_at", pa.timestamp("us")),
        ("fetched_month", pa.string()),
        ("complete", pa.bool_()),
        ("job_count", pa.int64()),
        ("rows_bronze", pa.int64()),
        ("normalize_errors", pa.int64()),
        ("connector", pa.string()),
        ("jobs_sha256", pa.string()),
    ]
)


@dataclass
class BadRun:
    path: str
    reason: str


@dataclass
class BronzeResult:
    processed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    bad: list[BadRun] = field(default_factory=list)
    rows: int = 0
    normalize_errors: int = 0
    warnings: list[str] = field(default_factory=list)


class _NoConfig:
    """Config ausente: qualquer acesso falha e vira normalize_error (nunca descarta a vaga)."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"config da fonte indisponivel (atributo '{name}')")


def _resolve_class(platform: str) -> type[BaseConnector] | None:
    # O slug do manifest usa hifen (lever-co); o registro usa underscore (lever_co).
    for name in dict.fromkeys((platform, platform.replace("-", "_"))):
        try:
            return get_connector_class(name)
        except ConnectorNotFound:
            continue
    return None


class ConnectorResolver:
    """Acha o conector (e a config) de uma run so com dados locais: catalog/sources.json."""

    def __init__(self, catalog_path: Path | None = None) -> None:
        self._configs: dict[tuple[str, str], dict[str, Any]] = {}
        self._cache: dict[tuple[str, str], BaseConnector | None] = {}
        path = catalog_path or DEFAULT_CATALOG_PATH
        if path.exists():
            for entry in read_catalog(path).sources:
                try:
                    cls = _resolve_class(entry.platform)
                    if cls is None:
                        continue
                    ext = cls.external_id_from_config(cls.validate_config(entry.config))
                except Exception:  # entrada invalida nao impede o resto
                    continue
                self._configs[(entry.platform, ext)] = entry.config

    def get(self, platform: str, source_external_id: str) -> BaseConnector | None:
        key = (platform, source_external_id)
        if key in self._cache:
            return self._cache[key]
        connector: BaseConnector | None
        cls = _resolve_class(platform)
        if cls is None:
            connector = None
        else:
            try:
                connector = cls(cast(HttpClient, None), **self._configs[key])
            except Exception:
                connector = cls.__new__(cls)
                connector.config = _NoConfig()  # type: ignore[assignment]
        self._cache[key] = connector
        return connector


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _error(exc: Exception) -> str:
    return safe_error(exc)[:500]


def _row(
    manifest: RawManifest,
    fetched_at: datetime,
    line_no: int,
    text: str,
    connector: BaseConnector | None,
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(JOBS_SCHEMA.names)
    row.update(
        run_id=manifest.run_id,
        source_external_id=manifest.source_external_id,
        platform=manifest.platform,
        fetched_at=fetched_at,
        fetched_month=fetched_at.strftime("%Y-%m"),
        complete=manifest.complete,
        payload_json=text,
    )
    try:
        record = json.loads(text)
        raw = RawJob(external_id=str(record["external_id"]), payload=record["payload"])
    except Exception as exc:
        row["normalize_error"] = f"linha {line_no} invalida: {_error(exc)}"
        return row
    row["external_id"] = raw.external_id
    row["payload_json"] = json.dumps(raw.payload, ensure_ascii=False, default=str)
    if connector is None:
        row["normalize_error"] = f"sem conector para a plataforma '{manifest.platform}'"
        return row
    try:
        normalized: NormalizedJob = connector.normalize(raw)
    except Exception as exc:
        row["normalize_error"] = _error(exc)
        return row
    row.update(
        title=normalized.title,
        company_name=normalized.company_name,
        description=normalized.description,
        location=normalized.location,
        original_url=normalized.original_url,
        application_url=normalized.application_url,
        salary_min=_float(normalized.salary_min),
        salary_max=_float(normalized.salary_max),
        salary_currency=normalized.salary_currency,
        salary_period=normalized.salary_period,
        employment_type=normalized.employment_type,
        workplace_type=normalized.workplace_type,
        original_posted_at=_utc_naive(normalized.original_posted_at),
    )
    return row


def _write_parquet(table: pa.Table, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".tmp-{uuid.uuid4().hex}.parquet"
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def bronze_paths(lake_dir: Path, month: str, run_id: str) -> tuple[Path, Path]:
    name = f"run_id={run_id}.parquet"
    return (
        lake_dir / "bronze" / "jobs" / f"month={month}" / name,
        lake_dir / "bronze" / "runs" / f"month={month}" / name,
    )


def _processed_run_ids(lake_dir: Path) -> set[str]:
    runs_dir = lake_dir / "bronze" / "runs"
    return {
        p.stem.removeprefix("run_id=")
        for p in runs_dir.glob("month=*/run_id=*.parquet")
        if not p.name.startswith(".")
    }


def iter_manifests(lake_dir: Path) -> list[Path]:
    """Manifests publicados do raw; ignora `.tmp-*` (run em escrita)."""
    found = sorted((lake_dir / "raw").glob("platform=*/source=*/dt=*/run_id=*/" + MANIFEST_FILE))
    return [p for p in found if not any(part.startswith(".tmp-") for part in p.parts)]


def _process_run(
    manifest_path: Path, lake_dir: Path, resolver: ConnectorResolver, result: BronzeResult
) -> None:
    run_dir = manifest_path.parent
    try:
        manifest = RawManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.bad.append(BadRun(str(run_dir), f"manifest invalido: {_error(exc)}"))
        return
    jobs_path = run_dir / manifest.jobs_file
    if not jobs_path.is_file():
        result.bad.append(BadRun(str(run_dir), f"arquivo ausente: {manifest.jobs_file}"))
        return
    actual = _sha256_file(jobs_path)
    if actual != manifest.jobs_sha256:
        result.bad.append(
            BadRun(str(run_dir), f"sha256 divergente (manifest {manifest.jobs_sha256[:12]}...)")
        )
        return

    fetched_at = _utc_naive(datetime.fromisoformat(manifest.fetched_at)) or datetime.now(UTC)
    connector = resolver.get(manifest.platform, manifest.source_external_id)
    rows: list[dict[str, Any]] = []
    with gzip.open(jobs_path, "rt", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.rstrip("\n")
            if text.strip():
                rows.append(_row(manifest, fetched_at, line_no, text, connector))
    errors = sum(1 for r in rows if r["normalize_error"])
    if len(rows) != manifest.job_count:
        result.warnings.append(
            f"{manifest.run_id}: manifest diz {manifest.job_count} vagas, arquivo tem {len(rows)}"
        )

    month = fetched_at.strftime("%Y-%m")
    jobs_out, runs_out = bronze_paths(lake_dir, month, manifest.run_id)
    _write_parquet(pa.Table.from_pylist(rows, schema=JOBS_SCHEMA), jobs_out)
    run_row = {
        "run_id": manifest.run_id,
        "source_external_id": manifest.source_external_id,
        "platform": manifest.platform,
        "fetched_at": fetched_at,
        "fetched_month": month,
        "complete": manifest.complete,
        "job_count": manifest.job_count,
        "rows_bronze": len(rows),
        "normalize_errors": errors,
        "connector": manifest.connector,
        "jobs_sha256": manifest.jobs_sha256,
    }
    # Por ultimo: so depois disto a run conta como processada.
    _write_parquet(pa.Table.from_pylist([run_row], schema=RUNS_SCHEMA), runs_out)
    result.processed.append(manifest.run_id)
    result.rows += len(rows)
    result.normalize_errors += errors


def build_bronze(
    lake_dir: Path | str, catalog_path: Path | None = None, reprocess: bool = False
) -> BronzeResult:
    """Processa as runs novas. `reprocess=True` refaz todas (sobrescreve de forma atomica)."""
    lake = Path(lake_dir)
    result = BronzeResult()
    done = set() if reprocess else _processed_run_ids(lake)
    resolver: ConnectorResolver | None = None
    for manifest_path in iter_manifests(lake):
        run_id = manifest_path.parent.name.removeprefix("run_id=")
        if run_id in done:
            result.skipped.append(run_id)
            continue
        if resolver is None:
            resolver = ConnectorResolver(catalog_path)
        _process_run(manifest_path, lake, resolver, result)
    for bad in result.bad:
        log.warning("raw ignorado: %s (%s)", bad.path, bad.reason)
    return result
