"""Constroi o historico do Hacker News (Who is hiring) a partir do raw ja baixado. Offline.

Uso: uv run python scripts/build_history.py [--lake-dir data]

(1) confere o sha256 de cada thread de data/history/hn/raw e grava o bronze em Parquet
(`data/history/hn/bronze/`, escrita atomica; thread inalterada e pulada); (2) roda so os modelos
dbt com tag `history` (silver_hn_*, gold_history_*) no warehouse do lake. Idempotente. Nao baixa
nada: o download e `scripts/load_history_hn.py`. Depois, refaca o dashboard com
`scripts/run_monthly.py --skip-collect`.
"""

import argparse
import sys
from pathlib import Path

import duckdb

from vaya_indexer.lake.history_build import build_history_bronze, history_root, run_history_build

ROOT = Path(__file__).resolve().parents[1]
TABLES = [
    "silver_hn_post_skill",
    "gold_history_skill_month",
    "gold_history_skill_year",
    "gold_history_coverage_month",
]


def _disk_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake-dir", default="data")
    args = parser.parse_args(argv)
    lake = Path(args.lake_dir)
    if not lake.is_absolute():
        lake = ROOT / lake

    result = build_history_bronze(lake)
    print(
        f"bronze: {len(result.processed)} threads novas/alteradas ({result.rows} posts), "
        f"{len(result.skipped)} ja prontas, {len(result.bad)} rejeitadas"
    )
    for bad in result.bad:
        print(f"  REJEITADA {bad.path}: {bad.reason}", file=sys.stderr)
    if result.bad:
        print("ERRO raw rejeitado: dbt do historico nao executado", file=sys.stderr)
        return 1
    try:
        warehouse = run_history_build(lake)
    except RuntimeError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for table in TABLES:
            row = con.execute(f"select count(*) from {table}").fetchone()
            print(f"{table}: {row[0] if row else 0} linhas")
    finally:
        con.close()
    bronze = history_root(lake) / "bronze"
    print(f"bronze em disco: {_disk_bytes(bronze) / 1_048_576:.1f} MiB em {bronze}")
    print(f"warehouse: {Path(warehouse).stat().st_size / 1_048_576:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
