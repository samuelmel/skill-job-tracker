"""Registra (ou limpa) um mes reprovado no gate de cobertura.

Uso: uv run python scripts/failed_months.py {mark,clear} [--month YYYY-MM] [--lake-dir data]

Sem --month usa o mes UTC atual (o mes da coleta). O raw nao e tocado; so o registro
`failed-months.json`, que silver/tendencia/dashboards passam a respeitar.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from vaya_indexer.lake.failed_months import clear_failed, mark_failed

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["mark", "clear"])
    parser.add_argument("--month")
    parser.add_argument("--lake-dir", default="data")
    args = parser.parse_args(argv)
    lake = Path(args.lake_dir)
    if not lake.is_absolute():
        lake = ROOT / lake
    month = args.month or datetime.now(UTC).strftime("%Y-%m")
    try:
        months = (mark_failed if args.action == "mark" else clear_failed)(lake, month)
    except ValueError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 2
    print(f"{args.action} {month}: meses reprovados = {months}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
