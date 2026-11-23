"""Bronze do historico HN e modelos dbt `history` em lake sintetico (tmp_path). Sem rede."""

import gzip
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest

from vaya_indexer.lake import history_build as hb
from vaya_indexer.lake import monthly
from vaya_indexer.lake.dashboard import (
    build_movers,
    collect_history,
    pp_delta,
    render_html,
    tenths,
)


def _write_thread(
    lake: Path, month: str, thread_id: int, texts: list[str], sha: str | None = None
) -> Path:
    thread_dir = lake / "history" / "hn" / "raw" / f"month={month}" / f"thread={thread_id}"
    thread_dir.mkdir(parents=True)
    posts = thread_dir / hb.POSTS_FILE
    with gzip.open(posts, "wt", encoding="utf-8") as fh:
        for i, text in enumerate(texts):
            rec = {
                "post_id": thread_id * 1000 + i,
                "thread_id": thread_id,
                "month": month,
                "created_at": f"{month}-05T10:00:00Z",
                "text": text,
            }
            fh.write(json.dumps(rec) + "\n")
    manifest: dict[str, Any] = {
        "thread_id": thread_id,
        "month": month,
        "title": f"Ask HN: Who is hiring? ({month})",
        "thread_created_at": f"{month}-01T10:00:00Z",
        "fetched_at": "2026-11-20T10:00:00Z",
        "post_count": len(texts),
        "api_top_level_count": len(texts),
        "firebase_kids_count": len(texts),
        "excluded_deleted_or_empty": 0,
        "complete": True,
        "posts_file": hb.POSTS_FILE,
        "sha256": sha or hashlib.sha256(posts.read_bytes()).hexdigest(),
    }
    (thread_dir / hb.MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    return thread_dir


def _bronze_files(lake: Path) -> list[Path]:
    return sorted((lake / "history" / "hn" / "bronze").rglob("*.parquet"))


def test_bronze_build_is_idempotent(tmp_path: Path) -> None:
    _write_thread(tmp_path, "2020-01", 1, ["Acme | Python", "Beta | Rust"])
    first = hb.build_history_bronze(tmp_path)
    assert first.processed == ["1"] and first.rows == 2 and not first.bad
    files = _bronze_files(tmp_path)
    assert len(files) == 2
    stamps = {p: p.stat().st_mtime_ns for p in files}
    second = hb.build_history_bronze(tmp_path)
    assert second.processed == [] and second.skipped == ["1"]
    assert {p: p.stat().st_mtime_ns for p in _bronze_files(tmp_path)} == stamps
    assert not list((tmp_path / "history").rglob(".tmp-*"))


def test_changed_raw_is_rebuilt(tmp_path: Path) -> None:
    thread_dir = _write_thread(tmp_path, "2020-01", 1, ["a"])
    hb.build_history_bronze(tmp_path)
    for child in thread_dir.iterdir():
        child.unlink()
    thread_dir.rmdir()
    _write_thread(tmp_path, "2020-01", 1, ["a", "b"])
    res = hb.build_history_bronze(tmp_path)
    assert res.processed == ["1"] and res.rows == 2


def test_invalid_sha256_fails_and_writes_nothing(tmp_path: Path) -> None:
    _write_thread(tmp_path, "2020-01", 1, ["Python"], sha="0" * 64)
    res = hb.build_history_bronze(tmp_path)
    assert len(res.bad) == 1 and "sha256" in res.bad[0].reason
    assert not res.processed and _bronze_files(tmp_path) == []
    with pytest.raises(RuntimeError, match="rejeitada"):
        hb.run_history_build(tmp_path)


def test_post_count_mismatch_is_rejected(tmp_path: Path) -> None:
    thread_dir = _write_thread(tmp_path, "2020-01", 1, ["a", "b"])
    manifest_path = thread_dir / hb.MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["post_count"] = 5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    res = hb.build_history_bronze(tmp_path)
    assert len(res.bad) == 1 and _bronze_files(tmp_path) == []


@pytest.fixture(scope="module")
def hist_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lake = tmp_path_factory.mktemp("lake_hist")
    base = ["Generic role at a startup"] * 26
    _write_thread(
        lake,
        "2021-03",
        10,
        [
            "Acme | Python and SQL | Remote",
            "Beta | Go programming | NYC",
            "Gamma | We love Scala and Rust",
            "Delta | Visit La Scala in Milan",
            "Eps | Go to market lead",
            "Zeta | Data work in R, Python",
            "Eta | rust-free shop, golang services",
            "Iota | ML Engineer | AI/ML",
            "Kappa | 5 ml of HTML and XML, html5 homl",
            "Lambda | MACHINE LEARNING platform",
            "Mu | Machine-learning and aprendizado de máquina",
            "Nu | uses sml and ocaml",
            *base,
        ],
    )
    _write_thread(lake, "2021-04", 11, ["Acme | python"] * 3)
    hb.run_history_build(lake)
    return lake


def _skills_of(lake: Path, text_prefix: str) -> set[str]:
    con = duckdb.connect(str(lake / "warehouse.duckdb"), read_only=True)
    try:
        # silver_hn_post e ephemeral (nao fica no warehouse): o texto vem do bronze.
        glob = (lake / "history" / "hn" / "bronze" / "posts" / "**" / "*.parquet").as_posix()
        rows = con.execute(
            "select s.skill from read_parquet(?, hive_partitioning = false) p "
            "join silver_hn_post_skill s using (post_id) where p.text like ?",
            [glob, text_prefix + "%"],
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def test_skill_classification_respects_case_and_exclusions(hist_lake: Path) -> None:
    assert {"Python", "SQL"} <= _skills_of(hist_lake, "Acme | Python and SQL")
    assert "Go" in _skills_of(hist_lake, "Beta")
    assert {"Scala", "Rust"} <= _skills_of(hist_lake, "Gamma")
    assert "Scala" not in _skills_of(hist_lake, "Delta")  # exclude_pattern
    assert "Go" not in _skills_of(hist_lake, "Eps")  # Go to market
    assert "R" in _skills_of(hist_lake, "Zeta")
    assert "Rust" not in _skills_of(hist_lake, "Eta")  # case_sensitive


def test_machine_learning_matches_acronym_and_case_variants(hist_lake: Path) -> None:
    ml = "Machine Learning"
    assert ml in _skills_of(hist_lake, "Iota")  # ML maiusculo e AI/ML
    assert ml in _skills_of(hist_lake, "Lambda")  # MACHINE LEARNING
    assert ml in _skills_of(hist_lake, "Mu")  # machine-learning / aprendizado de maquina
    assert ml not in _skills_of(hist_lake, "Kappa")  # ml minusculo, HTML, XML
    assert ml not in _skills_of(hist_lake, "Nu")  # sml / ocaml


def test_gold_denominator_is_top_level_posts_and_low_sample(hist_lake: Path) -> None:
    con = duckdb.connect(str(hist_lake / "warehouse.duckdb"), read_only=True)
    try:
        row = con.execute(
            "select n, denominator, low_sample from gold_history_skill_month "
            "where month = '2021-03' and skill = 'Python'"
        ).fetchone()
        assert row == (2, 38, False)
        cov = con.execute(
            "select n_posts, low_sample from gold_history_coverage_month where month = '2021-04'"
        ).fetchone()
        assert cov == (3, True)
    finally:
        con.close()


def test_history_build_is_idempotent_on_rerun(hist_lake: Path) -> None:
    again = hb.build_history_bronze(hist_lake)
    assert again.processed == [] and sorted(again.skipped) == ["10", "11"]


def test_dashboard_history_present_and_absent(hist_lake: Path, tmp_path: Path) -> None:
    con = duckdb.connect(str(hist_lake / "warehouse.duckdb"), read_only=True)
    try:
        hist = collect_history(con)
    finally:
        con.close()
    assert hist is not None
    assert hist["months"] == ["2021-03", "2021-04"]
    assert "author" not in json.dumps(hist).lower()
    empty = duckdb.connect(str(tmp_path / "empty.duckdb"))
    try:
        assert collect_history(empty) is None
    finally:
        empty.close()
    html = render_html({"hist": None, "months": []}, "2021-03", "")
    assert 'id="hist"' in html and "hidden" in html


def test_monthly_excludes_history_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_dbt(lake: Any, project_dir: Any = None, extra_args: Any = None) -> Path:
        captured["extra"] = extra_args
        raise RuntimeError("stop")

    monkeypatch.setattr(monthly, "run_dbt", fake_dbt)
    monkeypatch.setattr(monthly, "build_bronze", lambda *a, **k: None)
    (tmp_path / "bronze" / "runs").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="stop"):
        monthly.run_monthly(tmp_path)
    assert captured["extra"] == ["--exclude", "tag:history"]


def _yr(y: int, k: str, n: int, sh: float) -> dict[str, Any]:
    return {"y": y, "k": k, "n": n, "sh": sh}


def _movers_fixture() -> tuple[list[dict[str, Any]], dict[int, int], list[dict[str, Any]]]:
    yr = [
        _yr(2023, "A", 100, 10.0), _yr(2024, "A", 80, 8.0), _yr(2025, "A", 50, 5.0),
        _yr(2023, "B", 40, 4.0), _yr(2024, "B", 20, 2.0), _yr(2025, "B", 10, 1.0),
        _yr(2024, "D", 35, 3.0), _yr(2025, "D", 90, 9.0),
        _yr(2024, "E", 40, 4.0), _yr(2025, "E", 10, 1.0),
        _yr(2023, "JavaScript", 100, 9.0), _yr(2024, "JavaScript", 90, 8.0),
        _yr(2025, "JavaScript", 50, 4.0),
        _yr(2023, "TypeScript", 50, 5.0), _yr(2024, "TypeScript", 60, 6.0),
        _yr(2025, "TypeScript", 120, 12.0),
        _yr(2026, "A", 1, 0.1),
    ]  # fmt: skip
    months = {2023: 12, 2024: 12, 2025: 12, 2026: 11}
    jsts = [{"y": 2023, "n": 140, "sh": 13.0}, {"y": 2024, "n": 140, "sh": 12.5},
            {"y": 2025, "n": 160, "sh": 14.0}]  # fmt: skip
    return yr, months, jsts


def test_movers_use_last_complete_year_and_min_sample() -> None:
    yr, months, jsts = _movers_fixture()
    out = build_movers(yr, months, jsts, 30)
    assert out is not None
    head = out["pairs"][0]
    assert (head["a"], head["b"]) == (2024, 2025)  # 2026 e parcial: nao e a base da manchete
    assert [r["k"] for r in head["downs"]] == ["A", "E"]  # B (n<30 nos dois anos) fora
    assert head["downs"][0]["rel"] == -37.5 and head["downs"][0]["small"] is False
    assert head["downs"][1]["small"] is True  # E: ano anterior com 40 posts (< 50)
    assert head["downs"][0]["t"] == [10.0, 8.0, 5.0, 0.1]  # mini-tendencia alinhada aos anos


def test_javascript_and_typescript_never_appear_alone_and_combo_uses_per_post_value() -> None:
    yr, months, jsts = _movers_fixture()
    out = build_movers(yr, months, jsts, 30)
    assert out is not None
    for pair in out["pairs"]:
        for item in pair["ups"] + pair["downs"]:
            assert item["k"] not in ("JavaScript", "TypeScript")
    head = out["pairs"][0]
    ups = {r["k"]: r for r in head["ups"]}
    combo = ups["JavaScript + TypeScript"]
    assert (combo["psh"], combo["sh"], combo["dp"]) == (12.5, 14.0, 1.5)  # valor por post
    assert combo["skills"] == ["JavaScript", "TypeScript"]
    assert [r["k"] for r in head["ups"]] == ["D", "JavaScript + TypeScript"]


def test_movers_pairs_and_degenerate_inputs() -> None:
    yr, months, jsts = _movers_fixture()
    out = build_movers(yr, months, jsts, 30)
    assert out is not None
    labels = [(p["a"], p["b"]) for p in out["pairs"]]
    assert labels[0] == (2024, 2025) and (2023, 2026) not in labels  # sem 2022 nos dados
    assert (2025, 2026) in labels and out["pairs"][labels.index((2025, 2026))]["mb"] == 11
    assert build_movers(yr, {2025: 12}, jsts, 30) is None


def test_rounding_rule_is_single_and_consistent() -> None:
    # meio para cima em aritmetica inteira (24.35 nao vira 24.3 por erro de ponto flutuante)
    assert tenths(24.35) == 244
    assert tenths(8.45) == 85
    assert tenths(None) == 0
    # a variacao exibida e a diferenca dos valores exibidos: 21,0 -> 24,4 e +3,4 (nunca +3,3)
    assert pp_delta(21.01, 24.36) == 3.4
    assert pp_delta(21.0, 24.35) == 3.4
    assert pp_delta(5.0, 4.0) == -1.0


def test_movers_dp_matches_displayed_values() -> None:
    yr = [_yr(2024, "Python", 400, 21.01), _yr(2025, "Python", 450, 24.36)]
    out = build_movers(yr, {2024: 12, 2025: 12}, [], 30)
    assert out is not None
    row = out["pairs"][0]["ups"][0]
    assert row["dp"] == pp_delta(row["psh"], row["sh"]) == 3.4


def test_template_formats_pct_and_pp_through_single_functions() -> None:
    html = (Path(hb.__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    assert "sgn(" not in html
    assert html.count("var tenths") == 1 and html.count("var pp =") == 1
    # o explorador calcula a variacao pela mesma regra dos "mais subiram"
    assert "ppd(o.st.prev, o.st.cur)" in html
    assert "o.st.cur - o.st.prev" not in html


def test_warehouse_has_no_absolute_path_nor_view_over_files(hist_lake: Path) -> None:
    """silver_hn_post e ephemeral: nada de view sobre read_parquet nem copia do texto."""
    con = duckdb.connect(str(hist_lake / "warehouse.duckdb"), read_only=True)
    try:
        names = {
            r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()
        }
        views = con.execute(
            "select view_name, sql from duckdb_views() where not internal"
        ).fetchall()
    finally:
        con.close()
    assert "silver_hn_post" not in names
    lake_posix = hist_lake.resolve().as_posix()
    for name, sql in views:
        assert "read_parquet" not in sql and lake_posix not in sql, f"view {name} embute caminho"
    raw = (hist_lake / "warehouse.duckdb").read_bytes()
    assert lake_posix.encode() not in raw and str(hist_lake.resolve()).encode() not in raw


def test_rebuild_from_raw_in_new_dir_matches_original(hist_lake: Path, tmp_path: Path) -> None:
    """Copia so o raw para outro diretorio, reconstroi e compara; o bronze novo pode sumir."""
    new = tmp_path / "outro" / "lake"
    shutil.copytree(hist_lake / "history" / "hn" / "raw", new / "history" / "hn" / "raw")
    hb.run_history_build(new)
    queries = [
        "select * from gold_history_skill_month order by 1, 2, 3",
        "select * from gold_history_coverage_month order by 1",
        "select count(*) from silver_hn_post_skill",
    ]
    results = []
    for lake in (hist_lake, new):
        con = duckdb.connect(str(lake / "warehouse.duckdb"), read_only=True)
        try:
            results.append([con.execute(q).fetchall() for q in queries])
        finally:
            con.close()
    assert results[0] == results[1]
    # Sem o bronze, o warehouse novo continua legivel (nada aponta para fora dele).
    shutil.rmtree(new / "history" / "hn" / "bronze")
    con = duckdb.connect(str(new / "warehouse.duckdb"), read_only=True)
    try:
        assert con.execute("select count(*) from gold_history_skill_month").fetchone()
    finally:
        con.close()
