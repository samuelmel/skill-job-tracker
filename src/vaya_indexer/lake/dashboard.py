"""Dashboard estatico (um HTML autocontido por mes + index) gerado so da camada gold.

Sem CDN: CSS, JS e dados (JSON) ficam inline. Nada sintetico: se o warehouse nao tem dados,
a geracao falha em vez de inventar numeros.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
HEALTH_ROWS_LIMIT = 400
MIN_SAMPLE = 30
CATEGORY_LABELS = {
    "language": "Linguagens",
    "warehouse": "Armazéns e lakehouse",
    "processing": "Processamento",
    "database": "Bancos de dados",
    "orchestration": "Orquestração e integração",
    "streaming": "Streaming",
    "bi": "BI",
    "cloud": "Nuvem",
    "devops": "DevOps",
    "ml": "ML e IA",
    "web": "Web",
}


class NoDataError(RuntimeError):
    """O warehouse nao tem nenhum mes com coleta completa."""


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def collect_data(warehouse: Path) -> dict[str, Any]:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        coverage_rows = _rows(con, "select * from gold_month_coverage order by month")
        if not coverage_rows:
            raise NoDataError("gold_month_coverage vazio: nenhuma coleta no lake")
        coverage = {
            r["month"]: {k: (None if v is None else _num(v)) for k, v in r.items()}
            for r in coverage_rows
        }
        months = [r["month"] for r in coverage_rows if r["n_sources_complete"]]
        if not months:
            raise NoDataError("nenhum mes com coleta completa")
        skills = _rows(con, "select skill, category from skills order by skill")
        den: dict[str, dict[str, Any]] = {"all": {}, "data_roles": {}}
        for r in _rows(
            con,
            "select distinct month, scope, denominator, n_jobs, n_sources, low_sample "
            "from gold_skill_month",
        ):
            den[r["scope"]][r["month"]] = {
                "denominator": r["denominator"],
                "jobs": r["n_jobs"],
                "sources": r["n_sources"],
                "low": r["low_sample"],
            }
        sm = [
            {
                "m": r["month"],
                "s": r["scope"],
                "k": r["skill"],
                "n": r["n"],
                "d": r["denominator"],
                "sh": _num(r["share_pct"]),
                "rk": r["rank"],
                "lw": r["low_sample"],
            }
            for r in _rows(
                con, "select * from gold_skill_month where n > 0 order by month, scope, rank"
            )
        ]
        tr = [
            {
                "m": r["month"],
                "s": r["scope"],
                "k": r["skill"],
                "n": r["n"],
                "d": r["denominator"],
                "pn": r["prev_n"],
                "pd": r["prev_denominator"],
                "dp": _num(r["delta_pp"]),
                "rc": r["rank_change"],
                "st": r["status"],
            }
            for r in _rows(
                con,
                "select * from gold_skill_trend where n > 0 or prev_n > 0 order by month, scope",
            )
        ]
        summary = {
            r["health"]: r["n"]
            for r in _rows(con, "select health, count(*) as n from gold_source_health group by 1")
        }
        total = sum(summary.values())
        health_rows = [
            {
                "source_key": r["source_key"],
                "platform": r["platform"],
                "nc": r["n_runs_complete"],
                "np": r["n_runs_partial"],
                "last": str(r["last_fetched_at"])[:16],
                "jobs": r["last_run_jobs"],
                "pd": _num(r["pct_with_description"]),
                "health": r["health"],
            }
            for r in _rows(
                con,
                "select * from gold_source_health "
                "order by (health = 'ok'), health, n_runs_partial desc, source_key "
                f"limit {HEALTH_ROWS_LIMIT}",
            )
        ]
    finally:
        con.close()
    cats = sorted({s["category"] for s in skills})
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "min_sample": MIN_SAMPLE,
        "months": months,
        "coverage": coverage,
        "skills": skills,
        "cats": cats,
        "cat_labels": CATEGORY_LABELS,
        "den": den,
        "sm": sm,
        "tr": tr,
        "health": {
            "summary": summary,
            "total": total,
            "truncated": total > len(health_rows),
            "rows": health_rows,
        },
    }


def _json_for_script(data: dict[str, Any]) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


def render_html(data: dict[str, Any], default_month: str, links_html: str) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    return (
        html.replace("__REPORT_LINKS__", links_html)
        .replace("__DEFAULT_MONTH__", default_month)
        .replace("__DATA__", _json_for_script(data))
    )


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_reports(lake_dir: Path | str, month: str | None = None) -> list[Path]:
    """Gera dashboard-YYYY-MM.html para cada mes com dados e o index.html (mes mais recente)."""
    lake = Path(lake_dir)
    data = collect_data(lake / "warehouse.duckdb")
    months: list[str] = data["months"]
    default = month or months[-1]
    if default not in months:
        raise NoDataError(f"mes {default} sem coleta completa; disponiveis: {', '.join(months)}")
    out_dir = lake / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    links = (
        " ".join(f'<a href="dashboard-{m}.html">{m}</a>' for m in reversed(months))
        + ' <a href="index.html">mais recente</a>'
    )
    written: list[Path] = []
    for m in months:
        path = out_dir / f"dashboard-{m}.html"
        _write_atomic(path, render_html(data, m, links))
        written.append(path)
    index = out_dir / "index.html"
    _write_atomic(index, render_html(data, default, links))
    written.append(index)
    return written
