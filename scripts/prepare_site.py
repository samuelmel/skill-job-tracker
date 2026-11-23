"""Monta o diretorio do GitHub Pages so com os HTML do dashboard e confere que nada vaza.

Uso: uv run python scripts/prepare_site.py --reports data/reports --out _site

Copia apenas `index.html` e `dashboard-YYYY-MM.html`. Falha (codigo 1, nada publicado) se faltar
o index, se o diretorio de saida ja tiver conteudo, ou se algum HTML trouxer caminho local,
caminho de runner, referencia a raw/parquet/duckdb/jsonl.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = re.compile(r"^dashboard-\d{4}-(0[1-9]|1[0-2])\.html$")
FORBIDDEN = {
    # Letra de unidade + ":" + barra ou contrabarra, inclusive dentro de URL, JSON ou JS.
    "caminho Windows": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    "caminho de usuario": re.compile(r"/(Users|home|runner)/|[\\/]Users[\\/]", re.IGNORECASE),
    "caminho do runner": re.compile(r"/github/|[\\/]_work[\\/]"),
    "referencia a raw": re.compile(r"(?<![A-Za-z0-9_-])raw[\\/]", re.IGNORECASE),
    "arquivo de dados": re.compile(r"\.(duckdb|parquet|jsonl)\b|\.pgdata", re.IGNORECASE),
}


def scan(text: str) -> list[str]:
    return [label for label, rx in FORBIDDEN.items() if rx.search(text)]


def prepare(reports: Path, out: Path) -> list[Path]:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"diretorio de saida nao esta vazio: {out}")
    files = [reports / "index.html"] + sorted(
        p for p in reports.glob("dashboard-*.html") if DASHBOARD.match(p.name)
    )
    if not files[0].is_file():
        raise ValueError(f"index.html ausente em {reports}")
    if len(files) < 2:
        raise ValueError(f"nenhum dashboard-YYYY-MM.html em {reports}")
    for path in files:
        problems = scan(path.read_text(encoding="utf-8"))
        if problems:
            raise ValueError(f"{path.name} traz {', '.join(problems)}: nada publicado")
    out.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copyfile(path, out / path.name)
    return [out / p.name for p in files]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", default="data/reports")
    parser.add_argument("--out", default="_site")
    args = parser.parse_args(argv)
    reports, out = Path(args.reports), Path(args.out)
    if not reports.is_absolute():
        reports = ROOT / reports
    if not out.is_absolute():
        out = ROOT / out
    try:
        written = prepare(reports, out)
    except ValueError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"site: {path.name} ({path.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
