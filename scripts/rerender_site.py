"""Republica o front-end do dashboard sem recoletar: baixa o site publicado e o renderiza de novo.

Uso: uv run python scripts/rerender_site.py [--url URL] [--out _site]

Le o `index.html` publicado, extrai o JSON embutido, o mes padrao e os links de relatorios e
renderiza as paginas com o template atual (`render_html`). Os numeros nao mudam; so `areas`
(agrupamento do seletor, derivado das skills) e recalculado. A saida tem os mesmos arquivos de
`prepare_site.py`: `index.html` e `dashboard-YYYY-MM.html`. Falha (codigo 1, nada gravado) se o
JSON, o mes padrao ou os links nao forem achados, se faltar campo exigido pelo template, se o
diretorio de saida ja tiver conteudo ou se algum HTML trouxer caminho local ou arquivo de dados.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from prepare_site import scan

from vaya_indexer.lake.dashboard import build_areas, render_html

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://samuelmel.github.io/skill-job-tracker/"
USER_AGENT = "VayaJobIndexer/0.1 (+https://github.com/samuelmel/skill-job-tracker)"
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Campos que o template le de `D` (hist e opcional; areas e recalculado aqui).
REQUIRED = ("generated_at", "months", "coverage", "skills", "den", "sm", "tr", "health")

_DATA = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.DOTALL)
_MONTH_VAR = re.compile(r'var DEFAULT_MONTH = "([^"]*)";')
_LINKS = re.compile(r'<span id="links">(.*?)</span>', re.DOTALL)


def fetch(url: str, timeout: float = 30.0) -> str:
    resp = httpx.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
    )
    if resp.status_code != 200:
        raise ValueError(f"{url} respondeu {resp.status_code}; esperado 200")
    return resp.text


def extract(html: str) -> tuple[dict[str, Any], str, str]:
    """Devolve (dados, mes padrao, html dos links) de um dashboard ja renderizado."""
    m = _DATA.search(html)
    if not m:
        raise ValueError('JSON embutido (<script id="data">) nao encontrado na pagina')
    # Desfaz o escape de _json_for_script.
    raw = m.group(1).replace("<\\!--", "<!--").replace("<\\/", "</")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON embutido invalido: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON embutido nao e um objeto")
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError(f"JSON sem campos exigidos pelo template: {', '.join(missing)}")
    months = data["months"]
    if not (isinstance(months, list) and months and all(isinstance(x, str) for x in months)):
        raise ValueError("campo months vazio ou invalido")
    bad = [x for x in months if not MONTH.match(x)]
    if bad:
        raise ValueError(f"meses fora do formato YYYY-MM: {', '.join(bad)}")
    d = _MONTH_VAR.search(html)
    if not d or d.group(1) not in months:
        raise ValueError("mes padrao (DEFAULT_MONTH) ausente ou fora de months")
    links = _LINKS.search(html)
    if not links or not links.group(1).strip():
        raise ValueError('links de relatorios (<span id="links">) nao encontrados')
    return data, d.group(1), links.group(1)


def rerender(html: str) -> dict[str, str]:
    """Mapa nome de arquivo -> HTML, igual ao que write_reports gera (index + um por mes)."""
    data, default, links = extract(html)
    data["areas"] = build_areas(data["skills"])
    pages = {f"dashboard-{m}.html": render_html(data, m, links) for m in data["months"]}
    pages["index.html"] = render_html(data, default, links)
    for name, text in pages.items():
        problems = scan(text)
        if problems:
            raise ValueError(f"{name} traz {', '.join(problems)}: nada gravado")
    return pages


def write(pages: dict[str, str], out: Path) -> list[Path]:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"diretorio de saida nao esta vazio: {out}")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in pages.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", default="_site")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    try:
        pages = rerender(fetch(args.url))
        written = write(pages, out)
    except (ValueError, httpx.HTTPError) as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"site: {path.name} ({path.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
