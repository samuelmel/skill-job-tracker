from pathlib import Path

import duckdb
import pytest

from skilometer.lake import warehouse as wh


def _make(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("create table t as select range as i, repeat('x', 200) as s from range(20000)")
    con.execute("create schema silver")
    con.execute("create table silver.u as select 1 as a union all select 2")
    con.execute("create view v as select i from t where i < 10")
    con.execute("delete from t where i >= 100")  # deixa espaco livre para compactar
    con.close()


def _state(path: Path) -> tuple[int, int, int, list[str]]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        t = con.execute("select count(*) from t").fetchone()
        u = con.execute("select count(*) from silver.u").fetchone()
        v = con.execute("select count(*) from v").fetchone()
        views = [
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_type='VIEW'"
            ).fetchall()
        ]
    finally:
        con.close()
    assert t and u and v
    return t[0], u[0], v[0], views


def test_compact_success_keeps_data_and_views_and_cleans_temp(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    _make(db)
    before = _state(db)
    result = wh.compact_warehouse(db)
    assert _state(db) == before == (100, 2, 10, ["v"])
    assert (result.tables, result.views) == (2, 1)
    assert result.bytes_after <= result.bytes_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["w.duckdb"]


def test_compact_failure_preserves_original_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "w.duckdb"
    _make(db)
    raw = db.read_bytes()
    real = wh._count

    def bad(con: duckdb.DuckDBPyConnection, catalog: str, obj: str) -> int:
        n = real(con, catalog, obj)
        return n + 1 if catalog == "d" and obj == "main.t" else n

    monkeypatch.setattr(wh, "_count", bad)
    with pytest.raises(wh.CompactError, match="main.t"):
        wh.compact_warehouse(db)
    assert db.read_bytes() == raw
    assert _state(db) == (100, 2, 10, ["v"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["w.duckdb"]


def test_compact_quietly_swallows_errors(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    db.write_bytes(b"not a duckdb file")
    assert wh.compact_quietly(db) is None
    assert db.read_bytes() == b"not a duckdb file"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["w.duckdb"]


def test_missing_view_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "w.duckdb"
    _make(db)
    real = wh._objects

    def no_view(con: duckdb.DuckDBPyConnection, catalog: str) -> dict[str, str]:
        out = real(con, catalog)
        if catalog == "d":
            out.pop("main.v", None)
        return out

    monkeypatch.setattr(wh, "_objects", no_view)
    with pytest.raises(wh.CompactError, match="main.v"):
        wh.compact_warehouse(db)
    assert _state(db)[3] == ["v"]
