import gzip
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from skilometer.connectors.base import RawJob
from skilometer.lake.raw import RawLakeWriter, sanitize_part

RUN = uuid.UUID("11111111-1111-1111-1111-111111111111")
SRC = uuid.UUID("22222222-2222-2222-2222-222222222222")
FETCHED = datetime(2026, 11, 23, 23, 30, tzinfo=UTC)


def _jobs(n: int = 3) -> list[RawJob]:
    return [RawJob(external_id=f"j{i}", payload={"id": i, "t": "Engenheiro ção"}) for i in range(n)]


def _write(writer: RawLakeWriter, **over: object) -> object:
    args: dict[str, object] = {
        "platform": "greenhouse",
        "source_external_id": "acme",
        "source_id": SRC,
        "run_id": RUN,
        "fetched_at": FETCHED,
        "complete": True,
        "jobs": _jobs(),
        "connector": "GreenhouseConnector",
    }
    args.update(over)
    return writer.write(**args)  # type: ignore[arg-type]


def test_writes_layout_manifest_and_checksum(tmp_path: Path) -> None:
    result = _write(RawLakeWriter(tmp_path))
    run_dir = (
        tmp_path / "raw" / "platform=greenhouse" / "source=acme" / "dt=2026-11-23" / f"run_id={RUN}"
    )
    assert Path(result.path) == run_dir  # type: ignore[attr-defined]
    gz_path = run_dir / "jobs.jsonl.gz"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["jobs_sha256"] == hashlib.sha256(gz_path.read_bytes()).hexdigest()
    assert manifest["job_count"] == 3
    assert manifest["complete"] is True
    assert manifest["platform"] == "greenhouse"
    assert manifest["source_external_id"] == "acme"
    assert manifest["source_id"] == str(SRC)
    assert manifest["run_id"] == str(RUN)
    assert manifest["fetched_at"] == FETCHED.isoformat()
    assert manifest["connector"] == "GreenhouseConnector"
    assert manifest["schema_version"] == 1
    lines = gzip.decompress(gz_path.read_bytes()).decode("utf-8").splitlines()
    assert len(lines) == manifest["job_count"]
    assert json.loads(lines[1]) == {
        "external_id": "j1",
        "payload": {"id": 1, "t": "Engenheiro ção"},
    }


def test_partition_date_is_utc(tmp_path: Path) -> None:
    local = datetime(2026, 11, 23, 22, 0, tzinfo=timezone(timedelta(hours=-5)))  # 03:00 UTC
    result = _write(RawLakeWriter(tmp_path), fetched_at=local)
    assert "dt=2026-11-24" in result.path  # type: ignore[attr-defined]


def test_empty_listing_still_writes_manifest(tmp_path: Path) -> None:
    result = _write(RawLakeWriter(tmp_path), jobs=[], complete=False)
    manifest = json.loads((Path(result.path) / "manifest.json").read_text())  # type: ignore[attr-defined]
    assert manifest["job_count"] == 0 and manifest["complete"] is False


def test_never_overwrites_existing_run(tmp_path: Path) -> None:
    writer = RawLakeWriter(tmp_path)
    result = _write(writer)
    gz = Path(result.path) / "jobs.jsonl.gz"  # type: ignore[attr-defined]
    before = gz.read_bytes()
    with pytest.raises(FileExistsError):
        _write(writer, jobs=_jobs(1))
    assert gz.read_bytes() == before
    parent = Path(result.path).parent  # type: ignore[attr-defined]
    assert [p.name for p in parent.iterdir()] == [f"run_id={RUN}"]  # sem temp sobrando


def test_failure_mid_write_leaves_nothing_published(tmp_path: Path) -> None:
    circular: dict[str, object] = {}
    circular["self"] = circular  # json.dumps falha com ValueError
    bad = RawJob.model_construct(external_id="x", payload=circular)
    writer = RawLakeWriter(tmp_path)
    with pytest.raises(ValueError, match="Circular"):
        _write(writer, jobs=[*_jobs(2), bad])
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() or p.name.startswith("run_id=")]
    assert leftovers == []
    # a mesma run pode ser regravada depois da falha
    _write(writer)


def test_sanitize_blocks_traversal_and_avoids_collisions() -> None:
    assert sanitize_part("acme-1.2_x") == "acme-1.2_x"
    nasty = sanitize_part("../../etc/passwd")
    assert "/" not in nasty and ".." not in nasty and not nasty.startswith(".")
    assert sanitize_part("a/b") != sanitize_part("a_b")
    assert sanitize_part("") not in {"", ".", ".."}
    assert sanitize_part("..") not in {"", ".", ".."}


def test_path_parts_are_sanitized_in_layout(tmp_path: Path) -> None:
    result = _write(RawLakeWriter(tmp_path), source_external_id="https://x.com/a b/../c")
    path = Path(result.path).resolve()  # type: ignore[attr-defined]
    assert tmp_path.resolve() in path.parents
    assert path.relative_to(tmp_path.resolve()).parts[:2] == ("raw", "platform=greenhouse")
    assert len(path.relative_to(tmp_path.resolve()).parts) == 5
