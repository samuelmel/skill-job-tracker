"""Bronze offline: idempotencia, checksum, linhas invalidas preservadas. Sem rede."""

import gzip
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from vaya_indexer.connectors.base import RawJob
from vaya_indexer.lake.bronze import build_bronze
from vaya_indexer.lake.raw import JOBS_FILE, RawLakeWriter

FETCHED = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
NO_CATALOG = Path("nao-existe.json")


def _write_run(lake: Path, jobs: list[RawJob], platform: str = "lever-co") -> Path:
    writer = RawLakeWriter(lake)
    run_id = uuid.uuid4()
    result = writer.write(
        platform=platform,
        source_external_id="acme",
        source_id=uuid.uuid4(),
        run_id=run_id,
        fetched_at=FETCHED,
        complete=True,
        jobs=jobs,
        connector="LeverConnector",
    )
    return Path(result.path)


def _bronze_rows(lake: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for f in sorted((lake / "bronze" / "jobs").glob("month=*/run_id=*.parquet")):
        rows.extend(pq.read_table(f).to_pylist())
    return rows


def test_bronze_writes_partitioned_parquet_and_is_idempotent(tmp_path: Path) -> None:
    _write_run(tmp_path, [RawJob(external_id="1", payload={"text": "Data Engineer"})])
    first = build_bronze(tmp_path, NO_CATALOG)
    assert len(first.processed) == 1
    assert first.rows == 1
    files = list((tmp_path / "bronze" / "jobs" / "month=2026-11").glob("run_id=*.parquet"))
    assert len(files) == 1
    assert len(list((tmp_path / "bronze" / "runs" / "month=2026-11").glob("*.parquet"))) == 1

    second = build_bronze(tmp_path, NO_CATALOG)
    assert second.processed == []
    assert len(second.skipped) == 1
    assert len(_bronze_rows(tmp_path)) == 1
    assert not list(tmp_path.rglob(".tmp-*.parquet"))


def test_normalize_failure_keeps_the_row_with_error(tmp_path: Path) -> None:
    # Sem catalogo a config da fonte nao existe: o normalize falha, mas a vaga fica.
    _write_run(
        tmp_path,
        [RawJob(external_id="a", payload={"x": 1}), RawJob(external_id="b", payload={"x": 2})],
    )
    result = build_bronze(tmp_path, NO_CATALOG)
    assert result.rows == 2
    assert result.normalize_errors == 2
    rows = _bronze_rows(tmp_path)
    assert {r["external_id"] for r in rows} == {"a", "b"}
    assert all(r["normalize_error"] for r in rows)
    assert all(json.loads(str(r["payload_json"])) for r in rows)


def test_unknown_platform_is_recorded_not_dropped(tmp_path: Path) -> None:
    _write_run(tmp_path, [RawJob(external_id="1", payload={})], platform="plataforma-fantasma")
    result = build_bronze(tmp_path, NO_CATALOG)
    assert result.rows == 1
    (row,) = _bronze_rows(tmp_path)
    assert "sem conector" in str(row["normalize_error"])


def test_checksum_mismatch_is_rejected_and_reported(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [RawJob(external_id="1", payload={"a": 1})])
    jobs_path = run_dir / JOBS_FILE
    with gzip.open(jobs_path, "wb") as fh:
        fh.write(b'{"external_id": "1", "payload": {"a": 999}}\n')
    result = build_bronze(tmp_path, NO_CATALOG)
    assert result.processed == []
    assert len(result.bad) == 1
    assert "sha256" in result.bad[0].reason
    assert not (tmp_path / "bronze").exists() or not _bronze_rows(tmp_path)


def test_invalid_line_is_preserved_with_error(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [RawJob(external_id="1", payload={"a": 1})])
    # Reescreve o arquivo com uma linha lixo e atualiza o checksum do manifest.
    from vaya_indexer.lake.raw import _sha256_file

    jobs_path = run_dir / JOBS_FILE
    with gzip.open(jobs_path, "wb") as fh:
        fh.write(b'{"external_id": "1", "payload": {"a": 1}}\nnao-e-json\n')
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["jobs_sha256"] = _sha256_file(jobs_path)
    manifest["job_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = build_bronze(tmp_path, NO_CATALOG)
    assert result.rows == 2
    errors = [r for r in _bronze_rows(tmp_path) if "invalida" in str(r["normalize_error"])]
    assert len(errors) == 1
    assert errors[0]["payload_json"] == "nao-e-json"


def test_tmp_run_dirs_are_ignored(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [RawJob(external_id="1", payload={})])
    hidden = run_dir.parent / ".tmp-abc"
    hidden.mkdir()
    (hidden / "manifest.json").write_text("{}", encoding="utf-8")
    result = build_bronze(tmp_path, NO_CATALOG)
    assert len(result.processed) == 1
    assert result.bad == []
