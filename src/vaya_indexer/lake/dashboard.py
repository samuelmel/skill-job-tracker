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


HISTORY_TABLES = (
    "gold_history_skill_month",
    "gold_history_skill_year",
    "gold_history_coverage_month",
)


class NoDataError(RuntimeError):
    """O warehouse nao tem nenhum mes com coleta completa."""


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


SMALL_BASE_POSTS = 50
MOVER_TOP = 8
SPAN_YEARS = 4  # comparacao de prazo mais longo: 2026 vs 2022
# JavaScript e TypeScript sao um so movimento (o vocabulario migrou de um para o outro): nunca
# aparecem sozinhos em altas/quedas; entram como uma linha combinada, contada por post.
JSTS_SKILLS = ("JavaScript", "TypeScript")
JSTS_LABEL = "JavaScript + TypeScript"


def _mover(
    name: str,
    skills: list[str],
    a: tuple[float, int],
    b: tuple[float, int],
    trend: list[float],
) -> dict[str, Any]:
    (sa, na), (sb, nb) = a, b
    return {
        "k": name,
        "skills": skills,
        "psh": sa,
        "sh": sb,
        "dp": round(sb - sa, 2),
        "rel": round(100.0 * (sb - sa) / sa, 1) if sa else None,
        "pn": na,
        "n": nb,
        "small": 0 < na < SMALL_BASE_POSTS,
        "new": na == 0,
        "t": trend,
    }


def _pair_movers(
    yidx: dict[int, dict[str, dict[str, Any]]],
    jidx: dict[int, dict[str, Any]],
    years: list[int],
    a: int,
    b: int,
    min_sample: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    names = {k for y in (a, b) for k in yidx.get(y, {})}
    for skill in sorted(names - set(JSTS_SKILLS)):
        ra, rb = yidx.get(a, {}).get(skill), yidx.get(b, {}).get(skill)
        pa = (ra["sh"] or 0.0, ra["n"]) if ra else (0.0, 0)
        pb = (rb["sh"] or 0.0, rb["n"]) if rb else (0.0, 0)
        if max(pa[1], pb[1]) < min_sample:
            continue
        trend = [(yidx.get(y, {}).get(skill) or {}).get("sh") or 0.0 for y in years]
        items.append(_mover(skill, [skill], pa, pb, trend))
    ja, jb = jidx.get(a), jidx.get(b)
    if ja and jb and max(ja["n"], jb["n"]) >= min_sample:
        trend = [(jidx.get(y) or {}).get("sh") or 0.0 for y in years]
        items.append(
            _mover(JSTS_LABEL, list(JSTS_SKILLS), (ja["sh"], ja["n"]), (jb["sh"], jb["n"]), trend)
        )
    ups = sorted((i for i in items if i["dp"] > 0), key=lambda i: (-i["dp"], i["k"]))
    downs = sorted((i for i in items if i["dp"] < 0), key=lambda i: (i["dp"], i["k"]))
    return ups[:MOVER_TOP], downs[:MOVER_TOP]


def build_movers(
    yr: list[dict[str, Any]],
    months_by_year: dict[int, int],
    jsts: list[dict[str, Any]],
    min_sample: int,
) -> dict[str, Any] | None:
    """Altas e quedas por par de anos, com JavaScript/TypeScript tratados em um unico lugar.

    O par 0 e o ultimo ano COMPLETO vs. o anterior (nunca ano parcial como base de manchete);
    depois vem o prazo longo (ano final vs. ano final - 4) e os demais anos consecutivos.
    Entram skills com n >= min_sample posts em um dos dois anos.
    """
    years = sorted(months_by_year)
    if len(years) < 2:
        return None
    yidx: dict[int, dict[str, dict[str, Any]]] = {}
    for r in yr:
        yidx.setdefault(r["y"], {})[r["k"]] = r
    jidx = {r["y"]: r for r in jsts}
    consecutive = [(y - 1, y) for y in years if (y - 1) in months_by_year]
    complete = [b for _, b in consecutive if months_by_year[b] >= 12]
    head = (max(complete) - 1, max(complete)) if complete else consecutive[-1]
    pairs_ab = [head]
    last = years[-1]
    if (last - SPAN_YEARS) in months_by_year and (last - SPAN_YEARS, last) != head:
        pairs_ab.append((last - SPAN_YEARS, last))
    pairs_ab += [p for p in reversed(consecutive) if p not in pairs_ab]
    pairs = []
    for a, b in pairs_ab:
        ups, downs = _pair_movers(yidx, jidx, years, a, b, min_sample)
        pairs.append(
            {
                "a": a,
                "b": b,
                "span": b - a > 1,
                "ma": months_by_year[a],
                "mb": months_by_year[b],
                "ups": ups,
                "downs": downs,
            }
        )
    return {
        "years": years,
        "pairs": pairs,
        "small_base": SMALL_BASE_POSTS,
        "min_sample": min_sample,
    }


def collect_history(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    """Serie do Hacker News (tag history), separada da coleta atual. None se nao existir.

    Sem autor e sem texto de post: so contagens e participacoes por mes/ano.
    """
    have = {
        r[0]
        for r in con.execute(
            "select table_name from information_schema.tables where table_schema = 'main'"
        ).fetchall()
    }
    if not set(HISTORY_TABLES) <= have:
        return None
    coverage_rows = _rows(con, "select * from gold_history_coverage_month order by month")
    if not coverage_rows:
        return None
    coverage = {
        r["month"]: {k: (None if v is None else _num(v)) for k, v in r.items()}
        for r in coverage_rows
    }
    sm = [
        {"m": r["month"], "k": r["skill"], "n": r["n"], "d": r["denominator"], "sh": r["share_pct"]}
        for r in _rows(
            con,
            "select month, skill, n, denominator, share_pct from gold_history_skill_month "
            "where n > 0 order by month, n desc, skill",
        )
    ]
    yr = [
        {
            "y": r["year"],
            "k": r["skill"],
            "n": r["n"],
            "d": r["denominator"],
            "mo": r["months_covered"],
            "sh": _num(r["mean_month_share_pct"]),
            "pn": r["prev_n"],
            "dp": _num(r["delta_pp"]),
            "sg": r["has_signal"],
        }
        for r in _rows(
            con,
            "select * from gold_history_skill_year where n > 0 or coalesce(prev_n, 0) > 0 "
            "order by year, skill",
        )
    ]
    jsts: list[dict[str, Any]] = []
    if "silver_hn_post_skill" in have:
        # Posts com JavaScript OU TypeScript (cada post conta uma vez; nada de somar percentuais).
        jsts = [
            {"y": r["year"], "n": r["n"], "d": r["d"], "mo": r["mo"], "sh": _num(r["sh"])}
            for r in _rows(
                con,
                "select c.year, sum(coalesce(w.n, 0)) as n, sum(c.n_posts) as d, count(*) as mo, "
                "round(avg(100.0 * coalesce(w.n, 0) / c.n_posts), 2) as sh "
                "from gold_history_coverage_month c left join ("
                "select month, count(distinct post_id) as n from silver_hn_post_skill "
                "where skill in ('JavaScript', 'TypeScript') group by month"
                ") w on w.month = c.month group by c.year order by c.year",
            )
        ]
    months_by_year: dict[int, int] = {}
    for r in coverage_rows:
        months_by_year[r["year"]] = months_by_year.get(r["year"], 0) + 1
    return {
        "movers": build_movers(yr, months_by_year, jsts, MIN_SAMPLE),
        "jsts": jsts,
        "months": [r["month"] for r in coverage_rows],
        "years": sorted({r["year"] for r in coverage_rows}),
        "coverage": coverage,
        "sm": sm,
        "yr": yr,
        "min_sample": MIN_SAMPLE,
    }


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
        hist = collect_history(con)
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
        "hist": hist,
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
