"""Silver/gold via dbt sobre um lake sintetico em tmp_path (bronze escrito direto). Sem rede."""

import csv
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from skilometer.lake.bronze import JOBS_SCHEMA, RUNS_SCHEMA, bronze_paths
from skilometer.lake.dashboard import (
    FALLBACK_PLACE,
    PLACES,
    build_areas,
    collect_data,
    render_html,
)
from skilometer.lake.monthly import MONTHLY_DBT_ARGS
from skilometer.lake.transform import run_dbt

OCT = datetime(2026, 10, 10, 12, 0)
NOV = datetime(2026, 11, 10, 12, 0)


def _write_run(
    lake: Path,
    source: str,
    fetched_at: datetime,
    complete: bool,
    jobs: list[dict[str, Any]],
) -> None:
    run_id = str(uuid.uuid4())
    month = fetched_at.strftime("%Y-%m")
    rows = []
    for j in jobs:
        row: dict[str, Any] = dict.fromkeys(JOBS_SCHEMA.names)
        row.update(
            run_id=run_id,
            source_external_id=source,
            platform="test",
            fetched_at=fetched_at,
            fetched_month=month,
            complete=complete,
            external_id=j["id"],
            title=j.get("title", "Backend Developer"),
            company_name="Acme",
            description=j.get("description"),
            payload_json="{}",
        )
        rows.append(row)
    jobs_out, runs_out = bronze_paths(lake, month, run_id)
    jobs_out.parent.mkdir(parents=True, exist_ok=True)
    runs_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=JOBS_SCHEMA), jobs_out)
    run_row = {
        "run_id": run_id,
        "source_external_id": source,
        "platform": "test",
        "fetched_at": fetched_at,
        "fetched_month": month,
        "complete": complete,
        "job_count": len(rows),
        "rows_bronze": len(rows),
        "normalize_errors": 0,
        "connector": "Test",
        "jobs_sha256": "x",
    }
    pq.write_table(pa.Table.from_pylist([run_row], schema=RUNS_SCHEMA), runs_out)


def _jobs(
    prefix: str, total: int, with_python: int, no_description: int = 0, **extra: Any
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(total):
        job: dict[str, Any] = {"id": f"{prefix}{i}", **extra}
        if i < no_description:
            job["description"] = None
        elif i < no_description + with_python:
            job["description"] = "<p>We use Python every day.</p>"
        else:
            job["description"] = "<p>Generic duties and a great team.</p>"
        out.append(job)
    return out


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


@pytest.fixture(scope="module")
def trend_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lake = tmp_path_factory.mktemp("lake_trend")
    # Out: A (40 vagas, 4 com Python), B (10 vagas, 5 sem descricao), C PARTIAL (40, todas Python)
    _write_run(lake, "A", OCT, True, _jobs("a", 40, 4, title="Data Engineer"))
    _write_run(lake, "B", OCT, True, _jobs("b", 10, 0, no_description=5))
    _write_run(lake, "C", OCT, False, _jobs("c", 40, 40))
    # Nov: A (24 com Python), B igual, C completa (mas PARTIAL em out), D nova (todas Python)
    _write_run(lake, "A", NOV, True, _jobs("a", 40, 24, title="Data Engineer"))
    _write_run(lake, "B", NOV, True, _jobs("b", 10, 0, no_description=5))
    _write_run(lake, "C", NOV, True, _jobs("c", 40, 40))
    _write_run(lake, "D", NOV, True, _jobs("d", 40, 40))
    run_dbt(lake, extra_args=MONTHLY_DBT_ARGS)
    return lake


@pytest.fixture
def con(trend_lake: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(str(trend_lake / "warehouse.duckdb"), read_only=True)
    try:
        yield c
    finally:
        c.close()


def test_denominator_counts_only_jobs_with_description(con: duckdb.DuckDBPyConnection) -> None:
    n_jobs, denominator = _one(
        con,
        "select n_jobs, denominator from gold_skill_month "
        "where month = '2026-10' and scope = 'all' and skill = 'Python'",
    )
    # Out, so runs completas: A (40 vagas) + B (10, das quais 5 sem descricao); C PARTIAL fora.
    assert n_jobs == 50
    assert denominator == 45


def test_partial_run_is_excluded_from_month(con: duckdb.DuckDBPyConnection) -> None:
    n, sources = _one(
        con,
        "select n, n_sources from gold_skill_month "
        "where month = '2026-10' and scope = 'all' and skill = 'Python'",
    )
    assert n == 4  # as 40 vagas Python da fonte C (PARTIAL) nao contam
    assert sources == 2


def test_first_month_is_baseline_with_null_deltas(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "select status, delta_pp, rank_change, prev_month "
        "from gold_skill_trend where month = '2026-10'"
    ).fetchall()
    assert rows
    assert {r[0] for r in rows} == {"baseline"}
    assert all(r[1] is None and r[2] is None and r[3] is None for r in rows)


def test_panel_excludes_new_and_previously_partial_sources(
    con: duckdb.DuckDBPyConnection,
) -> None:
    panel_sources, n, den, prev_n, prev_den, delta, status = _one(
        con,
        "select panel_sources, n, denominator, prev_n, prev_denominator, delta_pp, status "
        "from gold_skill_trend where month = '2026-11' and scope = 'all' and skill = 'Python'",
    )
    # Painel de nov = fontes com run completa em out E nov: A e B. D (nova) e C (PARTIAL em
    # out) ficam de fora, senao a participacao de Python pareceria explodir por composicao.
    assert panel_sources == 2
    assert (n, den) == (24, 45)
    assert (prev_n, prev_den) == (4, 45)
    assert float(delta) == pytest.approx(44.44, abs=0.01)
    assert status == "rising"


def test_month_table_is_descriptive_and_includes_new_sources(
    con: duckdb.DuckDBPyConnection,
) -> None:
    n, den, sources = _one(
        con,
        "select n, denominator, n_sources from gold_skill_month "
        "where month = '2026-11' and scope = 'all' and skill = 'Python'",
    )
    assert sources == 4
    assert den == 40 + 5 + 40 + 40
    assert n == 24 + 40 + 40


def test_low_sample_never_rising_or_falling(con: duckdb.DuckDBPyConnection) -> None:
    (bad,) = _one(
        con,
        "select count(*) from gold_skill_trend "
        "where low_sample and status in ('rising', 'falling')",
    )
    assert bad == 0


def test_source_health_and_coverage(con: duckdb.DuckDBPyConnection) -> None:
    health = dict(con.execute("select source_key, health from gold_source_health").fetchall())
    assert set(health) == {"test/A", "test/B", "test/C", "test/D"}
    cov = con.execute(
        "select month, n_sources_complete from gold_month_coverage order by month"
    ).fetchall()
    assert cov == [("2026-10", 2), ("2026-11", 4)]


def test_dashboard_data_and_html(trend_lake: Path) -> None:
    data = collect_data(trend_lake / "warehouse.duckdb")
    assert data["months"] == ["2026-10", "2026-11"]
    assert data["min_sample"] == 30
    html = render_html(data, "2026-10", "")
    assert "__DATA__" not in html
    assert "<script src" not in html
    assert "https://" not in html
    # Marca e escopo: sem recorte de vagas de dados, favicon embutido e ajuda do JS+TS.
    assert "data_roles" not in html
    assert "Vagas de dados" not in html
    assert 'rel="icon" href="data:' in html
    assert "jsts-note" not in html
    assert "Skilometer" in html
    assert set(data["den"]) == {"all"}


def test_dashboard_areas_cover_every_skill(trend_lake: Path) -> None:
    data = collect_data(trend_lake / "warehouse.duckdb")
    in_areas = [s for a in data["areas"] for g in a["groups"] for s in g["skills"]]
    names = {s["skill"] for s in data["skills"]}
    assert names <= set(in_areas)
    assert set(in_areas) <= names
    assert [a["id"] for a in data["areas"]][0] == "lang"
    assert all(a["groups"] for a in data["areas"])


def test_dashboard_areas_real_seed_has_no_dumping_ground() -> None:
    seed = Path(__file__).resolve().parents[2] / "analytics" / "seeds" / "skills.csv"
    with seed.open(encoding="utf-8", newline="") as f:
        names = [r["skill"] for r in csv.DictReader(f)]
    assert not [n for n in names if n not in PLACES], "skill sem area explicita"
    areas = build_areas([{"skill": n} for n in names])
    assert FALLBACK_PLACE[0] not in {a["id"] for a in areas}
    for a in areas:
        assert sum(len(g["skills"]) for g in a["groups"]) <= 30, a["id"]


@pytest.fixture(scope="module")
def low_sample_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lake = tmp_path_factory.mktemp("lake_low")
    # 20 vagas com descricao por mes: abaixo de 30, mesmo com salto grande de Python.
    _write_run(lake, "A", OCT, True, _jobs("a", 20, 2))
    _write_run(lake, "A", NOV, True, _jobs("a", 20, 18))
    run_dbt(lake, extra_args=MONTHLY_DBT_ARGS)
    return lake


def test_denominator_under_30_is_low_sample_and_not_rising(low_sample_lake: Path) -> None:
    con = duckdb.connect(str(low_sample_lake / "warehouse.duckdb"), read_only=True)
    try:
        low, status, delta = _one(
            con,
            "select low_sample, status, delta_pp from gold_skill_trend "
            "where month = '2026-11' and scope = 'all' and skill = 'Python'",
        )
        (month_low,) = _one(
            con,
            "select low_sample from gold_skill_month "
            "where month = '2026-11' and scope = 'all' and skill = 'Python'",
        )
    finally:
        con.close()
    assert low is True
    assert month_low is True
    assert status == "low_sample"
    assert float(delta) == pytest.approx(80.0)  # o delta existe, mas nao vira rising


SKILL_CASES: list[tuple[str, str, bool]] = [
    # (descricao, skill, esperado)
    ("Experience with R and Python for modeling", "R", True),
    ("Skills: Python, SQL, R", "R", True),
    ("We are looking for a great team player in our area", "R", False),
    ("R&D department and R2D2 fans", "R", False),
    ("Backend in Go and Kubernetes", "Go", True),
    ("Golang services", "Go", True),
    ("Go-to-market strategy and we go further", "Go", False),
    ("Scala and Spark pipelines", "Scala", True),
    ("Tickets for La Scala opera", "Scala", False),
    ("Spark SQL jobs on Databricks", "Apache Spark", True),
    ("Bring your spark of curiosity", "Apache Spark", False),
    ("Transformations with dbt core", "dbt", True),
    ("Build ETL pipelines", "ETL/ELT", True),
    ("Retail stores and settlement", "ETL/ELT", False),
    ("Strong Python skills", "Python", True),
]


@pytest.fixture(scope="module")
def skill_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lake = tmp_path_factory.mktemp("lake_skills")
    jobs = [
        {
            "id": f"s{i}",
            "description": f"<p>{case[0]}. Plain filler to pass the minimum length.</p>",
        }
        for i, case in enumerate(SKILL_CASES)
    ]
    jobs += [
        {"id": "t1", "title": "Retail Store Associate", "description": "x"},
        {"id": "t2", "title": "ETL Developer", "description": "x"},
        {"id": "t3", "title": "Senior Data Engineer", "description": "x"},
    ]
    _write_run(lake, "A", OCT, True, jobs)
    run_dbt(lake, extra_args=MONTHLY_DBT_ARGS)
    return lake


def test_skill_regex_true_and_false_positives(skill_lake: Path) -> None:
    con = duckdb.connect(str(skill_lake / "warehouse.duckdb"), read_only=True)
    try:
        found = {
            (str(ext), str(skill))
            for ext, skill in con.execute(
                "select m.external_id, s.skill from silver_job_skill s "
                "join silver_job_month m using (job_month_key)"
            ).fetchall()
        }
    finally:
        con.close()
    for i, (text, skill, expected) in enumerate(SKILL_CASES):
        assert ((f"s{i}", skill) in found) is expected, f"{skill!r} em {text!r}"


def test_job_family_and_seniority_use_word_boundaries(skill_lake: Path) -> None:
    con = duckdb.connect(str(skill_lake / "warehouse.duckdb"), read_only=True)
    try:
        rows = con.execute(
            "select external_id, job_family, seniority from silver_job_observation "
            "where external_id like 't%'"
        ).fetchall()
    finally:
        con.close()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    assert by_id["t1"][0] == "operations_trades"  # "retail" nao vira "etl"
    assert by_id["t2"][0] == "data_engineer"
    assert by_id["t3"] == ("data_engineer", "senior")


def test_failed_month_is_excluded_from_site_and_trend_baseline(tmp_path: Path) -> None:
    """Mes reprovado no gate: raw/bronze ficam, mas nao entram em silver, tendencia nem site."""
    from skilometer.lake.failed_months import mark_failed, read_failed_months

    dec = datetime(2026, 12, 10, 12, 0)
    # Out boa; nov coletada pela metade (reprovada); dez boa.
    _write_run(tmp_path, "A", OCT, True, _jobs("a", 40, 4, title="Data Engineer"))
    _write_run(tmp_path, "A", NOV, True, _jobs("a", 40, 40, title="Data Engineer"))
    _write_run(tmp_path, "A", dec, True, _jobs("a", 40, 8, title="Data Engineer"))
    mark_failed(tmp_path, "2026-11")
    assert read_failed_months(tmp_path) == ["2026-11"]
    run_dbt(tmp_path, extra_args=MONTHLY_DBT_ARGS)

    data = collect_data(tmp_path / "warehouse.duckdb")
    assert data["months"] == ["2026-10", "2026-12"]
    assert '"2026-11"' not in render_html(data, "2026-12", "")
    con = duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True)
    try:
        assert _one(con, "select count(*) from gold_month_coverage where month = '2026-11'") == (0,)
        assert _one(con, "select count(*) from silver_run where fetched_month = '2026-11'") == (0,)
        # A base de comparacao de dez e outubro, nunca novembro.
        assert _one(
            con,
            "select distinct prev_month from gold_skill_trend where month = '2026-12'",
        ) == ("2026-10",)
        # Python: out 4/40 -> dez 8/40 (e nao 40/40 -> 8/40 de nov).
        assert _one(
            con,
            "select prev_n, n from gold_skill_trend "
            "where month = '2026-12' and scope = 'all' and skill = 'Python'",
        ) == (4, 8)
    finally:
        con.close()
    assert (tmp_path / "bronze" / "runs" / "month=2026-11").is_dir()  # derivado preservado


def test_failed_months_registry_roundtrip_and_validation(tmp_path: Path) -> None:
    from skilometer.lake.failed_months import clear_failed, mark_failed, read_failed_months

    assert read_failed_months(tmp_path) == []
    assert mark_failed(tmp_path, "2026-11") == ["2026-11"]
    assert mark_failed(tmp_path, "2026-11") == ["2026-11"]  # idempotente
    assert clear_failed(tmp_path, "2026-11") == []
    with pytest.raises(ValueError):
        mark_failed(tmp_path, "2026-13")
    (tmp_path / "failed-months.json").write_text("{nao e json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_failed_months(tmp_path)
