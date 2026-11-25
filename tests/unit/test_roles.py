"""Familia de cargo (seed role_families), salario anual em USD e JSON da secao Empregos."""

import csv
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from vaya_indexer.lake.bronze import JOBS_SCHEMA, RUNS_SCHEMA, bronze_paths
from vaya_indexer.lake.dashboard import collect_data
from vaya_indexer.lake.monthly import MONTHLY_DBT_ARGS
from vaya_indexer.lake.roles import (
    MIN_COMPANIES,
    MIN_SALARY_SAMPLE,
    annual_usd_for_job,
    collect_role_trend,
    collect_roles,
    role_trend_rows,
    round_thousand,
    salary_stats,
)
from vaya_indexer.lake.transform import run_dbt
from vaya_indexer.processing.salary import (
    annual_usd_from_text,
    annual_usd_midpoint,
    parse_salary,
)

SEED = Path(__file__).resolve().parents[2] / "analytics" / "seeds" / "role_families.csv"
NOV = datetime(2026, 11, 10, 12, 0)

TITLE_CASES = [
    ("Senior Machine Learning Engineer", "AI/ML Engineer"),
    ("ML Engineer II", "AI/ML Engineer"),
    ("Software Engineer, Machine Learning", "AI/ML Engineer"),
    ("LLM Application Developer", "AI/ML Engineer"),
    ("Data Engineer II", "Data Engineer"),
    ("Analytics Engineer", "Data Engineer"),
    ("Engenheiro de Dados Senior", "Data Engineer"),
    ("Senior Data Scientist", "Data Scientist"),
    ("Applied Scientist, Ads", "Data Scientist"),
    ("Data Analyst (m/f/d)", "Data Analyst"),
    ("Power BI Developer", "Data Analyst"),
    ("Software Engineer, Backend", "Backend"),
    ("Senior Backend Software Engineer - Infrastructure", "Backend"),
    ("Software Engineer - Frontend", "Frontend"),
    ("Full Stack Developer", "Fullstack"),
    ("Fullstack Software Engineer", "Fullstack"),
    ("Senior iOS Engineer", "Mobile"),
    ("Android Developer", "Mobile"),
    ("Site Reliability Engineer", "DevOps/SRE/Cloud"),
    ("Platform Engineer", "DevOps/SRE/Cloud"),
    ("Cloud Engineer", "DevOps/SRE/Cloud"),
    ("Senior Security Engineer", "Security"),
    ("Cybersecurity Analyst", "Security"),
    ("QA Automation Engineer", "QA/Test"),
    ("Software Development Engineer in Test", "QA/Test"),
    ("Senior Product Manager, Machine Learning", "Product Manager"),
    ("Staff Software Engineer", "Software Engineer"),
    ("Software Development Engineer II", "Software Engineer"),
    ("Python Developer", "Software Engineer"),
    ("Desenvolvedor Java Pleno", "Software Engineer"),
    # Fora: cargos nao tecnicos ou chefia nunca viram familia tecnica.
    ("Sales Engineer", "Outro"),
    ("Senior Solutions Engineer, Data Platform", "Outro"),
    ("Software Sales Engineer", "Outro"),
    ("Customer Support Engineer", "Outro"),
    ("Mechanical Engineer", "Outro"),
    ("Civil Engineer II", "Outro"),
    ("Engineering Manager, Backend", "Outro"),
    ("Director of Engineering", "Outro"),
    ("Account Executive - Platform & Security", "Outro"),
    ("Retail Store Associate", None),
    ("Registered Nurse", None),
    ("Security Guard", None),
    ("Business Developer", "Outro"),
]


def _classify(titles: list[str]) -> dict[str, str | None]:
    con = duckdb.connect(":memory:")
    try:
        with SEED.open(encoding="utf-8", newline="") as f:
            rules = [(int(r["ord"]), r["role"], r["pattern"]) for r in csv.DictReader(f)]
        con.execute("create table rules (ord int, role varchar, pattern varchar)")
        con.executemany("insert into rules values (?, ?, ?)", rules)
        con.execute("create table t (title varchar)")
        con.executemany("insert into t values (?)", [(t,) for t in titles])
        rows = con.execute(
            "select title, role from t left join rules on regexp_matches("
            "strip_accents(lower(title)), pattern) "
            "qualify row_number() over (partition by title order by ord) = 1"
        ).fetchall()
    finally:
        con.close()
    return dict(rows)


def test_seed_is_ordered_and_covers_the_requested_families() -> None:
    with SEED.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    ords = [int(r["ord"]) for r in rows]
    assert ords == sorted(set(ords))
    roles = {r["role"] for r in rows}
    assert roles == {
        "AI/ML Engineer",
        "Data Engineer",
        "Data Scientist",
        "Data Analyst",
        "Software Engineer",
        "Backend",
        "Frontend",
        "Fullstack",
        "Mobile",
        "DevOps/SRE/Cloud",
        "Security",
        "QA/Test",
        "Product Manager",
        "Outro",
    }
    # Exclusoes ("Outro") antes de qualquer familia: e a ordem que impede "Sales Engineer"
    # de virar Software Engineer.
    first_family = min(int(r["ord"]) for r in rows if r["role"] != "Outro")
    assert any(int(r["ord"]) < first_family for r in rows if r["role"] == "Outro")


def test_titles_map_to_the_expected_family() -> None:
    got = _classify([t for t, _ in TITLE_CASES])
    wrong = {t: (got[t], want) for t, want in TITLE_CASES if got[t] != want}
    assert not wrong


def test_annual_usd_midpoint_rules() -> None:
    assert annual_usd_midpoint(100_000, 140_000, "USD", "YEAR") == 120_000
    assert annual_usd_midpoint(150_000, 150_000, "USD", "YEAR") == 150_000
    assert annual_usd_midpoint(50, 70, "USD", "HOUR") is None
    assert annual_usd_midpoint(100_000, 140_000, "CAD", "YEAR") is None
    assert annual_usd_midpoint(None, 140_000, "USD", "YEAR") is None  # "up to": sem ponto medio
    assert annual_usd_midpoint(150_000, 100_000, "USD", "YEAR") is None
    assert annual_usd_midpoint(10_000, 12_000, "USD", "YEAR") is None  # abaixo de 20 mil
    assert annual_usd_midpoint(900_000, 1_100_000, "USD", "YEAR") == 1_000_000
    assert annual_usd_midpoint(1_100_000, 1_300_000, "USD", "YEAR") is None  # acima de 1 milhao


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Salary: $120,000 - $150,000 per year.", 135_000),
        ("Pay range $140,000 to $180,000 USD", 160_000),
        ("Base: $100,000 - $120,000 and bonus", 110_000),
        ("Compensation $120k - $150k", 135_000),
        ("$120k-150k plus 401k match", 135_000),
        ("The salary is $150,000 per year", 150_000),
        ("$150,000 annually", 150_000),
        ("US$100,000 - US$120,000 a year", 110_000),
        ("pay $50 - $70/hour", None),
        ("$60,000 - $80,000 per hour (contract)", None),
        ("$8,000 - $10,000 per month", None),
        ("$2,000 - $3,000 weekly", None),
        ("CA$120,000 - CA$150,000", None),
        ("CAD $120,000 - $150,000", None),
        ("CAD $120,000 - $150,000 per year", None),  # o limite superior nao volta sozinho
        ("MXN $500,000 - $600,000 / year", None),
        ("$80/hr ($160,000 - $200,000/yr)", None),  # periodo ambiguo na mesma frase
        ("$50,000 - $60,000 per project", None),
        ("$50,000 - $60,000 per 6 months", None),
        ("Budget $50,000 - $60,000", None),
        ("$90,000 - $110,000 contract role", None),
        ("$50,000 - $60,000 + equity", 55_000),  # equity nao e periodo
        ("up to $150,000/yr", None),
        ("from $90,000 per year", None),
        ("We raised $500,000,000 in funding", None),
        ("A 401k and $5,000 signing bonus", None),
        ("No pay information here", None),
        ("", None),
        (None, None),
    ],
)
def test_annual_usd_from_description(text: str | None, expected: int | None) -> None:
    assert annual_usd_from_text(text) == expected


def test_parse_salary_default_behavior_is_unchanged() -> None:
    # Sem o piso opcional, "$120,000 - $150,000" sem periodo continua ambiguo (None).
    assert parse_salary("$120,000 - $150,000") is None
    got = parse_salary("$120,000 - $150,000", assume_annual_from=10_000)
    assert got is not None
    assert (got.min, got.max, got.currency, str(got.period)) == (120_000, 150_000, "USD", "YEAR")
    # Palavra de periodo explicita vence o piso.
    monthly = parse_salary("$12,000 - $15,000 per month", assume_annual_from=10_000)
    assert monthly is not None and str(monthly.period) == "MONTH"
    assert parse_salary("$50 - $70", assume_annual_from=10_000) is None


def test_structured_salary_wins_and_blocks_description_fallback() -> None:
    desc = "We pay $100,000 - $120,000 per year."
    assert annual_usd_for_job(130_000, 150_000, "USD", "YEAR", None) == 140_000
    # Estruturado em hora: a descricao nao e lida (nem vem na consulta); sem texto -> None.
    assert annual_usd_for_job(50, 70, "USD", "HOUR", None) is None
    assert annual_usd_for_job(None, None, None, None, desc) == 110_000


def test_round_thousand_and_stats_need_a_minimum_sample() -> None:
    assert round_thousand(158_499) == 158_000
    assert round_thousand(158_500) == 159_000
    few_jobs = [(f"c{i % 10}", 100_000) for i in range(MIN_SALARY_SAMPLE - 1)]
    small = salary_stats(few_jobs)
    assert small == {
        "n_salary": 29,
        "n_companies": 10,
        "median_salary": None,
        "p25": None,
        "p75": None,
    }
    values = [(f"c{i}", 100_000 + 1_000 * i) for i in range(MIN_SALARY_SAMPLE)]
    stats = salary_stats(values)
    assert (stats["n_salary"], stats["n_companies"]) == (MIN_SALARY_SAMPLE, MIN_SALARY_SAMPLE)
    assert stats["median_salary"] == 115_000  # (114_000 + 115_000) / 2 = 114_500 -> milhar
    assert stats["p25"] < stats["median_salary"] < stats["p75"]


def test_stats_need_enough_companies_and_weight_them_equally() -> None:
    # 40 vagas, mas so 7 empresas: nao publica.
    seven = [(f"c{i % 7}", 150_000) for i in range(40)]
    out = salary_stats(seven)
    assert (out["n_salary"], out["n_companies"], out["median_salary"]) == (40, 7, None)
    # Uma empresa com 100 vagas a 200k pesa o mesmo que cada uma das 8 outras (100k..107k).
    dominant = [("big", 200_000)] * 100 + [(f"c{i}", 100_000 + 1_000 * i) for i in range(8)]
    out = salary_stats(dominant)
    assert (out["n_salary"], out["n_companies"]) == (108, 9)
    assert out["median_salary"] == 104_000  # mediana das 9 medianas, nao dos 108 valores
    assert out["p75"] == 106_000


def _write_lake(lake: Path, jobs: list[dict[str, Any]]) -> None:
    run_id = str(uuid.uuid4())
    month = NOV.strftime("%Y-%m")
    rows = []
    for j in jobs:
        row: dict[str, Any] = dict.fromkeys(JOBS_SCHEMA.names)
        row.update(company_name="Acme")
        row.update(
            run_id=run_id,
            source_external_id="A",
            platform="test",
            fetched_at=NOV,
            fetched_month=month,
            complete=True,
            payload_json="{}",
            **j,
        )
        rows.append(row)
    jobs_out, runs_out = bronze_paths(lake, month, run_id)
    jobs_out.parent.mkdir(parents=True, exist_ok=True)
    runs_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=JOBS_SCHEMA), jobs_out)
    run_row = {
        "run_id": run_id,
        "source_external_id": "A",
        "platform": "test",
        "fetched_at": NOV,
        "fetched_month": month,
        "complete": True,
        "job_count": len(rows),
        "rows_bronze": len(rows),
        "normalize_errors": 0,
        "connector": "Test",
        "jobs_sha256": "x",
    }
    pq.write_table(pa.Table.from_pylist([run_row], schema=RUNS_SCHEMA), runs_out)


@pytest.fixture(scope="module")
def roles_lake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lake = tmp_path_factory.mktemp("lake_roles")
    jobs: list[dict[str, Any]] = []
    for i in range(35):  # ML: salario so na descricao (ponto medio 160k), com Python e JS
        jobs.append(
            {
                "external_id": f"ml{i}",
                "company_name": f"Co{i}",  # 35 empresas, uma vaga cada
                "title": "Senior Machine Learning Engineer",
                "description": "<p>Python and JavaScript. Pay $150,000 - $170,000 per year.</p>",
            }
        )
    for i in range(35):  # Sales Engineer: fora de tudo
        jobs.append(
            {
                "external_id": f"se{i}",
                "title": "Sales Engineer",
                "description": "<p>Python. $300,000 - $320,000 per year.</p>",
            }
        )
    for i in range(5):  # estruturado em hora + descricao com faixa: nao pode virar anual
        jobs.append(
            {
                "external_id": f"da{i}",
                "title": "Data Analyst",
                "description": "<p>SQL. Pay $100,000 - $120,000 per year.</p>",
                "salary_min": 50.0,
                "salary_max": 70.0,
                "salary_currency": "USD",
                "salary_period": "HOUR",
            }
        )
    jobs.append(
        {
            "external_id": "da9",
            "title": "Data Analyst",
            "description": "<p>SQL and Excel.</p>",
            "salary_min": 90000.0,
            "salary_max": 110000.0,
            "salary_currency": "USD",
            "salary_period": "YEAR",
        }
    )
    _write_lake(lake, jobs)
    run_dbt(lake, extra_args=MONTHLY_DBT_ARGS)
    return lake


def test_collect_data_exposes_roles_and_skill_salary(roles_lake: Path) -> None:
    data = collect_data(roles_lake / "warehouse.duckdb")
    assert data["roles_month"] == "2026-11"
    by_role = {r["role"]: r for r in data["roles"]}
    assert set(by_role) == {"AI/ML Engineer", "Data Analyst"}  # Sales Engineer ficou de fora
    ml = by_role["AI/ML Engineer"]
    assert set(ml) >= {
        "role",
        "n_jobs",
        "share_pct",
        "n_salary",
        "n_companies",
        "median_salary",
        "p25",
        "p75",
        "top_skills",
    }
    assert ml["n_jobs"] == 35
    assert ml["share_pct"] == round(100 * 35 / 41, 2)
    assert (ml["n_salary"], ml["n_companies"], ml["median_salary"], ml["p25"], ml["p75"]) == (
        35,
        35,
        160_000,
        160_000,
        160_000,
    )
    skills = {s["skill"]: s["pct"] for s in ml["top_skills"]}
    assert skills["Python"] == 100.0
    assert skills["JavaScript + TypeScript"] == 100.0  # um item so; nao ha JavaScript sozinho
    assert "JavaScript" not in skills
    assert len(ml["top_skills"]) <= 8
    da = by_role["Data Analyst"]
    assert da["n_jobs"] == 6
    assert (da["n_salary"], da["n_companies"]) == (
        1,
        1,
    )  # so o estruturado USD/ano; hora nao e lida nem na descricao
    assert da["median_salary"] is None and da["p25"] is None and da["p75"] is None
    # skill_salary: so skills com n_salary >= 30 e n_companies >= 8.
    ss = {r["skill"]: r for r in data["skill_salary"]}
    assert ss["Python"] == {
        "skill": "Python",
        "n_salary": 35,
        "n_companies": 35,
        "n_salary_hn": 0,
        "n_salary_coleta": 35,
        "median_salary": 160_000,
    }
    assert (ml["n_salary_coleta"], ml["n_salary_hn"]) == (35, 0)
    meta = data["salary_meta"]
    assert meta["sources"] == ["coleta 2026-11"]  # sem historico HN no warehouse
    assert (meta["n_salary"], meta["n_companies"]) == (36, 36)  # 35 ML + 1 Data Analyst
    assert "mediana" in meta["rule"]
    assert ss["JavaScript + TypeScript"]["n_salary"] == 35
    assert "SQL" not in ss
    assert all(
        r["n_salary"] >= MIN_SALARY_SAMPLE and r["n_companies"] >= MIN_COMPANIES
        for r in data["skill_salary"]
    )


def test_collect_roles_is_none_without_role_tables() -> None:
    con = duckdb.connect(":memory:")
    try:
        assert collect_roles(con, "2026-11") is None
    finally:
        con.close()


def test_hn_posts_join_the_salary_base_with_dedupe(roles_lake: Path, tmp_path: Path) -> None:
    import shutil

    wh = tmp_path / "wh.duckdb"
    shutil.copy(roles_lake / "warehouse.duckdb", wh)
    con = duckdb.connect(str(wh))
    try:
        con.execute(
            "create table gold_history_coverage_month as select * from (values "
            + ", ".join(f"('2025-{m:02d}', 2025, true)" for m in range(1, 13))
            + ", ('2026-01', 2026, true)) t(month, year, complete)"
        )
        con.execute(
            "create table silver_hn_post_pay(post_id bigint, month varchar, year int, "
            "header varchar, text varchar, role varchar)"
        )
        con.execute("create table silver_hn_post_skill(post_id bigint, year int, skill varchar)")
        pay = "We pay $150,000 - $190,000 per year."  # ponto medio 170k
        posts = [(i, f"HnCo{i} | ML Engineer | Remote", "AI/ML Engineer") for i in range(20)]
        posts.append((100, "https://hnco0.com HnCo0 | ML Engineer II", "AI/ML Engineer"))  # dup
        posts.append((101, "Co0 | ML Engineer", "AI/ML Engineer"))  # mesma empresa da coleta
        posts.append((102, "NoFamily | Barista", "Barista"))  # familia fora da coleta
        for post_id, header, role in posts:
            con.execute(
                "insert into silver_hn_post_pay values (?, '2025-03', 2025, ?, ?, ?)",
                [post_id, header, header + chr(10) + pay, role],
            )
            con.execute("insert into silver_hn_post_skill values (?, 2025, 'Python')", [post_id])
        out = collect_roles(con, "2026-11")
    finally:
        con.close()
    assert out is not None
    ml = next(r for r in out["roles"] if r["role"] == "AI/ML Engineer")
    # 35 da coleta + 20 HN + 1 da empresa Co0 no HN; o duplicado HnCo0 e o Barista saem.
    assert (ml["n_salary_coleta"], ml["n_salary_hn"], ml["n_salary"]) == (35, 21, 56)
    assert ml["n_companies"] == 55  # Co0 aparece nas duas fontes e conta uma vez
    assert ml["median_salary"] is not None
    assert (ml["n_jobs"], ml["share_pct"]) == (35, round(100 * 35 / 41, 2))  # so a coleta
    py = next(r for r in out["skill_salary"] if r["skill"] == "Python")
    assert (py["n_salary_hn"], py["n_salary_coleta"], py["n_companies"]) == (21, 35, 55)
    assert out["salary_meta"]["sources"] == ["coleta 2026-11", "Hacker News 2025"]
    assert out["salary_meta"]["n_salary"] == 57  # + 1 Data Analyst


def test_role_trend_rows_use_the_skill_trend_rules() -> None:
    prev = {"A": 300, "B": 100, "C": 5, "D": 100, "E": 20}
    cur = {"A": 200, "B": 200, "C": 25, "D": 101, "E": 25}
    rows = {r["role"]: r for r in role_trend_rows(prev, cur)}
    assert rows["A"]["signal"] == "down" and rows["A"]["delta_pp"] < -0.5
    assert rows["B"]["signal"] == "up" and rows["B"]["z"] > 1.96
    assert rows["D"]["signal"] == "flat"  # variacao pequena
    assert rows["C"]["small_base"] is True and rows["E"]["small_base"] is True
    assert rows["A"]["small_base"] is False
    assert (rows["A"]["n_prev"], rows["A"]["n_cur"]) == (300, 200)
    assert rows["A"]["share_prev"] == round(100 * 300 / 525, 2)
    assert set(rows["A"]) == {
        "role", "share_prev", "share_cur", "delta_pp", "n_prev", "n_cur", "z", "signal",
        "small_base",
    }  # fmt: skip


def test_collect_role_trend_needs_two_complete_years() -> None:
    con = duckdb.connect(":memory:")
    try:
        assert collect_role_trend(con) is None
        con.execute(
            "create table gold_history_coverage_month as select * from (values "
            + ", ".join(
                f"('{y}-{m:02d}', {y}, true, 100)" for y in (2024, 2025) for m in range(1, 13)
            )
            + ") t(month, year, complete, n_posts)"
        )
        con.execute(
            "create table silver_hn_post_role(post_id bigint, month varchar, year int, role varchar)"  # noqa: E501
        )
        rows = [(i, 2024, "A") for i in range(40)] + [(100 + i, 2025, "B") for i in range(40)]
        rows += [(200 + i, 2025, "A") for i in range(10)]
        for post_id, year, role in rows:
            con.execute(
                "insert into silver_hn_post_role values (?, ?, ?, ?)",
                [post_id, f"{year}-01", year, role],
            )
        out = collect_role_trend(con)
    finally:
        con.close()
    assert out is not None
    assert out["meta"] == {
        "year_prev": 2024,
        "year_cur": 2025,
        "n_posts_prev": 1200,
        "n_posts_cur": 1200,
        "n_with_role_prev": 40,
        "n_with_role_cur": 50,
    }
    by = {r["role"]: r for r in out["rows"]}
    assert by["B"]["signal"] == "up" and by["A"]["signal"] == "down"
    assert by["A"]["small_base"] is True  # n_cur=10 < 30


def test_collect_data_exposes_role_trend_optional_key(roles_lake: Path) -> None:
    assert "role_trend" not in collect_data(roles_lake / "warehouse.duckdb")  # sem historico HN


def test_hn_company_needs_a_pipe_header() -> None:
    from vaya_indexer.lake.roles import hn_company

    assert hn_company("https://acme.io Acme Inc. | ML Engineer | Remote") == "acme inc"
    assert hn_company("Hiring a senior ML engineer, remote, $200k") == ""  # sem "|"
