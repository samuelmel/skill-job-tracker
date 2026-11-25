"""Carga mensal completa: coleta todas as fontes do catalogo e depois processa o lake.

Uso: uv run python scripts/run_monthly.py [--data-dir .pgdata-lake] [--lake-dir data]
         [--month YYYY-MM] [--concurrency N] [--limit N] [--platform slug] [--skip-collect]

Ordem: (1) Postgres embutido + migrations + catalogo + fetch-all em todas as sources ativas;
(2) bronze + dbt + dashboard (`run_monthly`); (3) resumo. Se toda source falhar, ou menos de 50%
das tentadas terminar SUCCEEDED/PARTIAL, o dashboard NAO e refeito e o codigo de saida e != 0
(o raw ja gravado fica). O historico do Hacker News e uma carga avulsa e nao passa por aqui.
"""

import argparse
import sys
from pathlib import Path

from collect_local import ROOT, collect

from skilometer.lake.monthly import run_monthly, validate_month

MIN_OK_RATIO = 0.5


def threshold_error(summary: dict[str, int]) -> str | None:
    attempted = summary["attempted"]
    if attempted == 0:
        return "nenhuma source tentada: dashboard nao refeito"
    ok = summary["succeeded"] + summary["partial"]
    if ok == 0:
        return f"todas as {attempted} sources falharam: dashboard nao refeito"
    if ok / attempted < MIN_OK_RATIO:
        return (
            f"so {ok}/{attempted} sources SUCCEEDED/PARTIAL (minimo {MIN_OK_RATIO:.0%}): "
            "dashboard nao refeito"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=".pgdata-lake")
    parser.add_argument("--lake-dir", default="data")
    parser.add_argument("--month", help="mes YYYY-MM do index (padrao: ultimo com dados)")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--platform")
    parser.add_argument(
        "--skip-collect", action="store_true", help="so processa o raw que ja esta no lake"
    )
    args = parser.parse_args(argv)

    try:
        if args.month:
            validate_month(args.month)
    except ValueError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 2

    summary: dict[str, int] | None = None
    if not args.skip_collect:
        _, summary = collect(
            args.data_dir,
            args.lake_dir,
            limit=args.limit,
            platform=args.platform,
            concurrency=args.concurrency,
        )
        if summary is None:
            print("ERRO a coleta nao produziu resumo: dashboard nao refeito", file=sys.stderr)
            return 1
        print(
            f"coleta: {summary['attempted']} sources, {summary['succeeded']} SUCCEEDED, "
            f"{summary['partial']} PARTIAL, {summary['failed']} FAILED; "
            f"{summary['jobs_found']} vagas; {summary['raw_failures']} falhas de raw"
        )
        problem = threshold_error(summary)
        if problem:
            print(f"ERRO {problem} (raw ja gravado foi mantido)", file=sys.stderr)
            return 1

    lake = ROOT / args.lake_dir
    try:
        result = run_monthly(lake, args.month)
    except RuntimeError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 1

    print(
        f"mes {result.month}: {len(result.bronze.processed)} runs novas no bronze, "
        f"{len(result.bronze.bad)} rejeitadas"
    )
    for bad in result.bronze.bad:
        print(f"  REJEITADA {bad.path}: {bad.reason}")
    if summary is None:
        print("coleta: pulada (--skip-collect)")
    else:
        print(
            f"resumo: SUCCEEDED={summary['succeeded']} PARTIAL={summary['partial']} "
            f"FAILED={summary['failed']} vagas={summary['jobs_found']} "
            f"falhas_raw={summary['raw_failures']}"
        )
    print(f"mes: {result.month}")
    for path in result.reports:
        print(f"relatorio: {Path(path)}")
    wh = Path(lake) / "warehouse.duckdb"
    if wh.exists():
        print(f"warehouse: {wh.stat().st_size / 1_048_576:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
