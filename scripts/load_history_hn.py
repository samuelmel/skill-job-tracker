"""Carga historica mensal do "Ask HN: Who is hiring?" para data/history/hn/raw.

Uso: uv run python scripts/load_history_hn.py [--from YYYY-MM] [--to YYYY-MM] [--lake-dir data]
Idempotente: meses ja completos sao pulados; o mes mais novo e sempre baixado de novo.
"""

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.lake.history_hn import USER_AGENT, LoadSummary, load_history

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(value: str) -> str:
    if not _MONTH.match(value):
        raise argparse.ArgumentTypeError("use o formato YYYY-MM")
    return value


def _disk_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def _print(summary: LoadSummary, lake_dir: Path) -> None:
    months = sorted(summary.loaded + summary.skipped_complete)
    print("== resumo HN Who is hiring ==")
    print(f"threads descobertas: {len(summary.discovered)}")
    if months:
        print(f"meses no intervalo: {len(months)} ({months[0]} .. {months[-1]})")
    print(
        f"baixadas agora: {len(summary.loaded)}  "
        f"puladas (ja completas): {len(summary.skipped_complete)}"
    )
    print(f"total de posts: {summary.total_posts}")
    print(f"meses ausentes na serie: {summary.missing_months or 'nenhum'}")
    print(f"meses duplicados: {summary.duplicate_months or 'nenhum'}")
    print(
        f"titulos ignorados (parecem 'hiring' mas nao casam): {summary.skipped_titles or 'nenhum'}"
    )
    print(f"meses incompletos: {summary.incomplete or 'nenhum'}")
    print(f"falhas: {summary.failed or 'nenhuma'}")
    raw = lake_dir / "history" / "hn"
    print(f"disco: {_disk_bytes(raw) / 1_048_576:.1f} MiB em {raw}")


async def _run(args: argparse.Namespace) -> int:
    lake_dir = Path(args.lake_dir)
    # 1 req/s por host (Algolia e Firebase), retry com backoff em 429/5xx
    async with HttpClient(
        user_agent=USER_AGENT, timeout=60.0, max_retries=5, min_interval=1.0, backoff_max=60.0
    ) as http:
        summary = await load_history(http, lake_dir, start=args.start, end=args.end)
    _print(summary, lake_dir)
    return 1 if summary.failed or summary.incomplete else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=_month, default=None)
    parser.add_argument("--to", dest="end", type=_month, default=None)
    parser.add_argument("--lake-dir", default="data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
