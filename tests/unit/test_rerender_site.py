"""scripts/rerender_site.py: extrai os dados do site publicado e renderiza com o template atual."""

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx
import yaml

from skilometer.lake import dashboard
from skilometer.lake.dashboard import build_areas, render_html

ROOT = Path(__file__).resolve().parents[2]
URL = "https://example.test/site/"
LINKS = '<a href="dashboard-2026-02.html">2026-02</a> <a href="index.html">mais recente</a>'


@pytest.fixture
def mod(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    sys.modules.pop("rerender_site", None)
    return __import__("rerender_site")


def _data() -> dict[str, Any]:
    skills = [
        {"skill": "Python", "category": "lang"},
        {"skill": "Docker", "category": "infra"},
    ]
    return {
        "generated_at": "2026-02-01 06:30 UTC",
        "months": ["2026-01", "2026-02"],
        "coverage": {"2026-02": {"month": "2026-02", "texto": "a</b><!-- x -->"}},
        "skills": skills,
        "den": {},
        "sm": {},
        "tr": {},
        "health": {"summary": {}, "total": 0, "truncated": False, "rows": []},
        "areas": [{"id": "velho", "label": "Velho", "groups": []}],
        "hist": None,
    }


def _published(data: dict[str, Any] | None = None) -> str:
    return render_html(data or _data(), "2026-02", LINKS)


def test_roundtrip_preserva_dados_mes_e_links(mod: ModuleType) -> None:
    data = _data()
    got, month, links = mod.extract(_published(data))
    assert got == data
    assert month == "2026-02"
    assert links == LINKS


def test_rerender_so_muda_areas(mod: ModuleType) -> None:
    pages = mod.rerender(_published())
    assert set(pages) == {"index.html", "dashboard-2026-01.html", "dashboard-2026-02.html"}
    data, month, _ = mod.extract(pages["index.html"])
    expected = _data()
    expected["areas"] = build_areas(expected["skills"])
    assert data == expected
    assert month == "2026-02"
    assert mod.extract(pages["dashboard-2026-01.html"])[1] == "2026-01"


def test_html_sai_com_o_template_atual(mod: ModuleType) -> None:
    pages = mod.rerender(_published())
    template = dashboard.TEMPLATE.read_text(encoding="utf-8")
    head = template.split("__REPORT_LINKS__")[0]
    assert pages["index.html"].startswith(head)
    assert "__DATA__" not in pages["index.html"]


def test_pagina_sem_json_falha(mod: ModuleType) -> None:
    with pytest.raises(ValueError, match="JSON embutido"):
        mod.extract("<html><body>nada</body></html>")


def test_campo_exigido_ausente_falha(mod: ModuleType) -> None:
    data = _data()
    del data["tr"]
    with pytest.raises(ValueError, match="tr"):
        mod.rerender(_published(data))


def test_mes_padrao_fora_de_months_falha(mod: ModuleType) -> None:
    html = _published().replace('DEFAULT_MONTH = "2026-02"', 'DEFAULT_MONTH = "2030-01"')
    with pytest.raises(ValueError, match="DEFAULT_MONTH"):
        mod.extract(html)


def test_saida_nao_vazia_falha_sem_gravar(mod: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "x").write_text("1")
    with pytest.raises(ValueError, match="nao esta vazio"):
        mod.write({"index.html": "<p/>"}, tmp_path)
    assert not (tmp_path / "index.html").exists()


@respx.mock
def test_main_baixa_e_grava(mod: ModuleType, tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, text=_published()))
    out = tmp_path / "site"
    assert mod.main(["--url", URL, "--out", str(out)]) == 0
    assert "Skilometer" in route.calls[0].request.headers["user-agent"]
    assert "@" not in route.calls[0].request.headers["user-agent"]
    assert sorted(p.name for p in out.iterdir()) == [
        "dashboard-2026-01.html",
        "dashboard-2026-02.html",
        "index.html",
    ]


@respx.mock
def test_main_status_diferente_de_200_nao_gera_saida(mod: ModuleType, tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404, text="nao"))
    out = tmp_path / "site"
    assert mod.main(["--url", URL, "--out", str(out)]) == 1
    assert not out.exists()


def test_workflow_dashboard() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/dashboard.yml").read_text(encoding="utf-8"))
    on = wf[True] if True in wf else wf["on"]
    assert on["push"]["paths"] == [
        "src/skilometer/lake/dashboard_template.html",
        "scripts/rerender_site.py",
        ".github/workflows/dashboard.yml",
    ]
    assert wf["concurrency"] == {"group": "dashboard-frontend", "cancel-in-progress": True}
    text = (ROOT / ".github/workflows/dashboard.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    assert json.dumps(wf["jobs"]["deploy"]["permissions"], sort_keys=True) == json.dumps(
        {"id-token": "write", "pages": "write"}, sort_keys=True
    )
