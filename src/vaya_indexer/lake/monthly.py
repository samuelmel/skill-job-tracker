"""Rotina mensal offline: bronze + dbt build + dashboard, lendo so do lake."""

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from vaya_indexer.lake.bronze import BronzeResult, build_bronze
from vaya_indexer.lake.dashboard import write_reports
from vaya_indexer.lake.transform import run_dbt
from vaya_indexer.lake.warehouse import compact_quietly

MONTHLY_DBT_ARGS = ["--exclude", "tag:history"]
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass
class MonthlyResult:
    month: str
    bronze: BronzeResult
    reports: list[Path]


def validate_month(value: str) -> str:
    if not _MONTH.match(value):
        raise ValueError(f"mes invalido '{value}', use YYYY-MM")
    return value


def latest_month_with_data(warehouse: Path) -> str | None:
    """Ultimo mes com ao menos uma coleta completa no gold."""
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        row = con.execute(
            "select max(month) from gold_month_coverage where n_sources_complete > 0"
        ).fetchone()
    finally:
        con.close()
    return str(row[0]) if row and row[0] else None


def run_monthly(
    lake_dir: Path | str,
    month: str | None = None,
    catalog_path: Path | None = None,
    project_dir: Path | str | None = None,
    reprocess: bool = False,
) -> MonthlyResult:
    lake = Path(lake_dir)
    if month is not None:
        validate_month(month)
    bronze = build_bronze(lake, catalog_path, reprocess)
    if not (lake / "bronze" / "runs").is_dir():
        raise RuntimeError(f"nenhum raw processado em {lake / 'raw'}: nada para analisar")
    # Historico (tag history) tem build proprio: o mensal nao o recria nem le o disco dele.
    warehouse = run_dbt(lake, project_dir, extra_args=MONTHLY_DBT_ARGS)
    chosen = month or latest_month_with_data(warehouse)
    if chosen is None:
        raise RuntimeError("nenhum mes com coleta completa no warehouse")
    reports = write_reports(lake, chosen)
    # Depois do dashboard e com as conexoes fechadas: se falhar, o relatorio ja gerado vale.
    compact_quietly(warehouse)
    return MonthlyResult(month=chosen, bronze=bronze, reports=reports)
