"""Registro dos meses cuja coleta reprovou no gate de cobertura.

O raw desses meses fica preservado, mas silver, tendencia e dashboards os ignoram: um mes
incompleto nao pode aparecer no site do mes seguinte nem virar base de comparacao. O arquivo
`failed-months.json` mora na raiz do lake e viaja com o raw para o repositorio de dados.
"""

import json
import os
import re
from pathlib import Path

FAILED_MONTHS_FILE = "failed-months.json"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def failed_months_path(lake: Path) -> Path:
    return lake / FAILED_MONTHS_FILE


def _check(month: str) -> str:
    if not _MONTH.match(month):
        raise ValueError(f"mes invalido '{month}', use YYYY-MM")
    return month


def read_failed_months(lake: Path | str) -> list[str]:
    """Meses reprovados, ordenados. Arquivo ausente = nenhum; arquivo corrompido = erro."""
    path = failed_months_path(Path(lake))
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        months = data["failed_months"]
        if not isinstance(months, list):
            raise TypeError("failed_months nao e lista")
        return sorted({_check(str(m)) for m in months})
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{path.name} invalido: {type(exc).__name__}") from exc


def _write(lake: Path, months: list[str]) -> None:
    path = failed_months_path(lake)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(
        json.dumps({"failed_months": sorted(set(months))}, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def mark_failed(lake: Path | str, month: str) -> list[str]:
    lake = Path(lake)
    months = sorted({*read_failed_months(lake), _check(month)})
    _write(lake, months)
    return months


def clear_failed(lake: Path | str, month: str) -> list[str]:
    lake = Path(lake)
    months = [m for m in read_failed_months(lake) if m != _check(month)]
    _write(lake, months)  # sempre grava: o primeiro mes bom cria `{"failed_months": []}`
    return months
