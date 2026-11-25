"""Secao "Empregos" do dashboard: vagas, salario anual em USD e skills por familia de cargo.

Mes mais recente, escopo 'all', mesma populacao do restante do painel (silver_job_month de run
completa). A familia vem do seed `role_families` (modelo dbt `silver_job_role`); vaga sem familia
ou na familia "Outro" (cargo nao tecnico, ex.: Sales Engineer) fica fora de tudo daqui.

Denominadores: `share_pct` = vagas da familia / vagas tecnicas classificadas; `top_skills[].pct`
= vagas da familia COM descricao que citam a skill / vagas da familia com descricao (mesma regra
do denominador de gold_skill_month). JavaScript e TypeScript contam como um so item, por vaga,
como em altas/quedas. Salario: ver `skilometer.processing.salary` (USD, anual, ponto medio);
percentis por interpolacao linear; valores arredondados ao milhar mais proximo. Salario pesa cada
empresa igual (mediana das medianas por empresa); sem n_salary >= 30 e n_companies >= 8
mediana e quartis ficam None. Empresa = company_name, ou a fonte quando vem vazio.

Base salarial = coleta do mes + posts do Hacker News "Who is hiring" do ultimo ano completo
(`silver_hn_post_pay`, cargo pela 1a linha; skills de `silver_hn_post_skill`), mesmo parser.
Dedupe por (fonte, empresa normalizada, familia): uma observacao por chave. `n_jobs`,
`share_pct` e `top_skills` continuam so da coleta.
"""

import math
import re
import statistics
from collections import Counter
from typing import Any

import duckdb

from skilometer.processing.salary import annual_usd_from_text, annual_usd_midpoint

EXCLUDED_ROLE = "Outro"
MIN_SALARY_SAMPLE = 30
MIN_COMPANIES = 8
TOP_SKILLS = 8
JSTS_SKILLS = ("JavaScript", "TypeScript")
JSTS_LABEL = "JavaScript + TypeScript"
SRC_COLETA = "coleta"
SRC_HN = "hn"

# (fonte, empresa, familia, valor, skills)
Observation = tuple[str, str, str, int, set[str]]


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        r[0]
        for r in con.execute(
            "select table_name from information_schema.tables where table_schema = 'main'"
        ).fetchall()
    }


def normalize_company(name: str | None) -> str:
    """Chave de empresa: minusculas, sem URL nem pontuacao (mesma regra para coleta e HN)."""
    text = re.sub(r"https?://\S+|www\.\S+", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def hn_company(header: str) -> str:
    """Empresa do post do HN: 1o segmento do cabecalho (antes do primeiro '|'), normalizado.

    Sem '|' o cabecalho inteiro nao e um nome de empresa: devolve "" e o post fica fora.
    """
    if "|" not in header:
        return ""
    return normalize_company(header.split("|", 1)[0])


def latest_complete_hn_year(con: duckdb.DuckDBPyConnection) -> int | None:
    """Ultimo ano com os 12 meses no historico do HN, todos com threads completas."""
    if "gold_history_coverage_month" not in _tables(con):
        return None
    row = con.execute(
        "select year from gold_history_coverage_month group by year "
        "having count(distinct month) = 12 and bool_and(complete) order by year desc limit 1"
    ).fetchone()
    return int(row[0]) if row else None


def round_thousand(value: float) -> int:
    """Milhar mais proximo, meio para cima (sem o arredondamento do banqueiro do round())."""
    return int(math.floor(value / 1000 + 0.5)) * 1000


def salary_stats(values: list[tuple[str, int]]) -> dict[str, Any]:
    """Salario por empresa com peso igual: (empresa, valor) -> n_salary, n_companies, quartis.

    Mediana, p25 e p75 saem das MEDIANAS POR EMPRESA (uma empresa com 100 vagas pesa o mesmo que
    uma com 1). So sao publicados com n_salary >= MIN_SALARY_SAMPLE e n_companies >=
    MIN_COMPANIES; senao ficam None.
    """
    by_company: dict[str, list[int]] = {}
    for company, value in values:
        by_company.setdefault(company, []).append(value)
    n, n_companies = len(values), len(by_company)
    out: dict[str, Any] = {
        "n_salary": n,
        "n_companies": n_companies,
        "median_salary": None,
        "p25": None,
        "p75": None,
    }
    if n < MIN_SALARY_SAMPLE or n_companies < MIN_COMPANIES:
        return out
    medians = [statistics.median(v) for v in by_company.values()]
    q1, q2, q3 = statistics.quantiles(medians, n=4, method="inclusive")
    out.update(median_salary=round_thousand(q2), p25=round_thousand(q1), p75=round_thousand(q3))
    return out


def annual_usd_for_job(
    low: float | None,
    high: float | None,
    currency: str | None,
    period: str | None,
    text: str | None,
) -> int | None:
    """Estruturado manda; a descricao so e lida se a vaga nao tem salario estruturado nenhum."""
    if low is not None or high is not None or currency or period:
        return annual_usd_midpoint(low, high, currency, period)
    return annual_usd_from_text(text)


def _merge_jsts(skills: set[str]) -> set[str]:
    if skills & set(JSTS_SKILLS):
        return (skills - set(JSTS_SKILLS)) | {JSTS_LABEL}
    return skills


def dedupe_observations(obs: list[Observation]) -> list[Observation]:
    """Uma observacao por (fonte, empresa, familia): a primeira, na ordem recebida."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Observation] = []
    for o in obs:
        key = (o[0], o[1], o[2])
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out


def _stats(obs: list[Observation]) -> dict[str, Any]:
    stats = salary_stats([(o[1], o[3]) for o in obs])
    stats["n_salary_hn"] = sum(1 for o in obs if o[0] == SRC_HN)
    stats["n_salary_coleta"] = sum(1 for o in obs if o[0] == SRC_COLETA)
    return stats


def _hn_observations(con: duckdb.DuckDBPyConnection, year: int) -> list[Observation]:
    if not {"silver_hn_post_pay", "silver_hn_post_skill"} <= _tables(con):
        return []
    skills: dict[int, set[str]] = {}
    for post_id, skill in con.execute(
        "select post_id, skill from silver_hn_post_skill where year = ?", [year]
    ).fetchall():
        skills.setdefault(post_id, set()).add(skill)
    out: list[Observation] = []
    for post_id, header, text, role in con.execute(
        "select post_id, header, text, role from silver_hn_post_pay where year = ? "
        "order by post_id",
        [year],
    ).fetchall():
        value = annual_usd_from_text(text)
        company = hn_company(header)
        if value is None or not company:
            continue
        out.append((SRC_HN, company, role, value, _merge_jsts(skills.get(post_id, set()))))
    return out


def collect_roles(con: duckdb.DuckDBPyConnection, month: str) -> dict[str, Any] | None:
    """Roles, skill_salary e salary_meta; None se o warehouse nao tem as familias de cargo."""
    if not {"silver_job_role", "silver_job_month", "silver_job_skill"} <= _tables(con):
        return None
    jobs = con.execute(
        "select m.job_month_key, r.role, m.has_description, m.salary_min, m.salary_max, "
        "m.salary_currency, m.salary_period, "
        "case when m.salary_min is null and m.salary_max is null and m.salary_currency is null "
        "and m.salary_period is null then m.description_clean end, "
        "lower(trim(coalesce(nullif(trim(m.company_name), ''), m.source_key))) "
        "from silver_job_month m join silver_job_role r using (job_month_key) "
        "where m.run_complete and m.fetched_month = ? and r.role <> ? order by m.job_month_key",
        [month, EXCLUDED_ROLE],
    ).fetchall()
    if not jobs:
        return None
    skills_by_job: dict[str, set[str]] = {}
    for key, skill in con.execute(
        "select job_month_key, skill from silver_job_skill where fetched_month = ?",
        [month],
    ).fetchall():
        skills_by_job.setdefault(key, set()).add(skill)

    n_jobs: Counter[str] = Counter()
    n_desc: Counter[str] = Counter()
    skill_counts: dict[str, Counter[str]] = {}
    observations: list[Observation] = []
    for key, role, has_desc, lo, hi, cur, per, text, company in jobs:
        n_jobs[role] += 1
        found = _merge_jsts(skills_by_job.get(key, set()))
        if has_desc:
            n_desc[role] += 1
            skill_counts.setdefault(role, Counter()).update(found)
        value = annual_usd_for_job(lo, hi, cur, per, text)
        norm = normalize_company(company)
        if value is not None and norm:
            observations.append((SRC_COLETA, norm, role, value, found))
    hn_year = latest_complete_hn_year(con)
    if hn_year is not None:
        observations.extend(o for o in _hn_observations(con, hn_year) if o[2] in n_jobs)
    observations = dedupe_observations(observations)

    by_role: dict[str, list[Observation]] = {}
    # Limitacao conhecida: apos o dedupe cada (fonte, empresa, familia) guarda so as skills da
    # PRIMEIRA observacao; as demais vagas da mesma empresa/familia nao somam skills aqui.
    by_skill: dict[str, list[Observation]] = {}
    for o in observations:
        by_role.setdefault(o[2], []).append(o)
        for skill in o[4]:
            by_skill.setdefault(skill, []).append(o)

    total = sum(n_jobs.values())
    roles = []
    for role, n in sorted(n_jobs.items(), key=lambda kv: (-kv[1], kv[0])):
        top = sorted(skill_counts.get(role, Counter()).items(), key=lambda kv: (-kv[1], kv[0]))
        roles.append(
            {
                "role": role,
                "n_jobs": n,
                "share_pct": round(100.0 * n / total, 2),
                "n_with_description": n_desc[role],
                **_stats(by_role.get(role, [])),
                "top_skills": [
                    {"skill": s, "pct": round(100.0 * c / n_desc[role], 2)}
                    for s, c in top[:TOP_SKILLS]
                ],
            }
        )
    skill_salary = []
    for skill, obs in sorted(by_skill.items()):
        stats = _stats(obs)
        if stats["median_salary"] is not None:
            skill_salary.append(
                {
                    "skill": skill,
                    "n_salary": stats["n_salary"],
                    "n_companies": stats["n_companies"],
                    "n_salary_hn": stats["n_salary_hn"],
                    "n_salary_coleta": stats["n_salary_coleta"],
                    "median_salary": stats["median_salary"],
                }
            )
    skill_salary.sort(key=lambda r: (-r["median_salary"], r["skill"]))
    sources = [f"coleta {month}"]
    if any(o[0] == SRC_HN for o in observations):
        sources.append(f"Hacker News {hn_year}")
    salary_meta = {
        "sources": sources,
        "n_salary": len(observations),
        "n_companies": len({o[1] for o in observations}),
        "rule": (
            "USD anual, ponto medio da faixa; uma observacao por empresa e familia em cada "
            f"fonte; mediana das medianas por empresa; so com {MIN_SALARY_SAMPLE}+ salarios "
            f"e {MIN_COMPANIES}+ empresas"
        ),
    }
    return {
        "month": month,
        "roles": roles,
        "skill_salary": skill_salary,
        "salary_meta": salary_meta,
    }


MIN_TREND_SAMPLE = 30
Z_THRESHOLD = 1.96
MIN_DELTA_PP = 0.5


def _z_two_proportions(n1: int, d1: int, n2: int, d2: int) -> float | None:
    """z-teste de duas proporcoes com variancia agrupada (mesma conta de gold_skill_trend)."""
    if d1 <= 0 or d2 <= 0:
        return None
    pooled = (n1 + n2) / (d1 + d2)
    var = pooled * (1 - pooled) * (1 / d1 + 1 / d2)
    if var <= 0:
        return None
    return (n2 / d2 - n1 / d1) / math.sqrt(var)


def role_trend_rows(prev: dict[str, int], cur: dict[str, int]) -> list[dict[str, Any]]:
    """Participacao de cada familia entre posts COM cargo, ano anterior vs atual.

    Mesmas regras das skills: |z| >= 1,96 e variacao >= 0,5 p.p. para up/down; base pequena
    quando a familia tem n < 30 em qualquer dos anos (o sinal e calculado mesmo assim).
    """
    d_prev, d_cur = sum(prev.values()), sum(cur.values())
    rows: list[dict[str, Any]] = []
    for role in sorted(set(prev) | set(cur)):
        n_prev, n_cur = prev.get(role, 0), cur.get(role, 0)
        share_prev = 100.0 * n_prev / d_prev if d_prev else 0.0
        share_cur = 100.0 * n_cur / d_cur if d_cur else 0.0
        delta = share_cur - share_prev
        z = _z_two_proportions(n_prev, d_prev, n_cur, d_cur)
        signal = "flat"
        if z is not None and z >= Z_THRESHOLD and delta >= MIN_DELTA_PP:
            signal = "up"
        elif z is not None and z <= -Z_THRESHOLD and delta <= -MIN_DELTA_PP:
            signal = "down"
        rows.append(
            {
                "role": role,
                "share_prev": round(share_prev, 2),
                "share_cur": round(share_cur, 2),
                "delta_pp": round(delta, 2),
                "n_prev": n_prev,
                "n_cur": n_cur,
                "z": None if z is None else round(z, 2),
                "signal": signal,
                "small_base": n_prev < MIN_TREND_SAMPLE or n_cur < MIN_TREND_SAMPLE,
            }
        )
    rows.sort(key=lambda r: (-abs(r["delta_pp"]), r["role"]))
    return rows


def collect_role_trend(con: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    """Tendencia de cargos no HN: ultimo ano completo vs o anterior (tambem completo)."""
    have = _tables(con)
    if not {"silver_hn_post_role", "gold_history_coverage_month"} <= have:
        return None
    complete = [
        int(r[0])
        for r in con.execute(
            "select year from gold_history_coverage_month group by year "
            "having count(distinct month) = 12 and bool_and(complete) order by year"
        ).fetchall()
    ]
    if len(complete) < 2 or complete[-1] - complete[-2] != 1:
        return None
    y_prev, y_cur = complete[-2], complete[-1]
    counts: dict[int, dict[str, int]] = {y_prev: {}, y_cur: {}}
    for year, role, n in con.execute(
        "select year, role, count(*) from silver_hn_post_role where year in (?, ?) group by 1, 2",
        [y_prev, y_cur],
    ).fetchall():
        counts[int(year)][role] = int(n)
    posts = dict(
        con.execute(
            "select year, sum(n_posts) from gold_history_coverage_month where year in (?, ?) "
            "group by 1",
            [y_prev, y_cur],
        ).fetchall()
    )
    meta = {
        "year_prev": y_prev,
        "year_cur": y_cur,
        "n_posts_prev": int(posts.get(y_prev, 0)),
        "n_posts_cur": int(posts.get(y_cur, 0)),
        "n_with_role_prev": sum(counts[y_prev].values()),
        "n_with_role_cur": sum(counts[y_cur].values()),
    }
    if not meta["n_with_role_prev"] or not meta["n_with_role_cur"]:
        return None
    return {"meta": meta, "rows": role_trend_rows(counts[y_prev], counts[y_cur])}
