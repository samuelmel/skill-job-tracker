"""scripts/run_monthly.py com coleta e processamento mockados. Sem rede, sem Postgres."""

import ast
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def rm(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("run_monthly", None)
    import run_monthly

    return run_monthly


def _summary(succeeded: int = 0, partial: int = 0, failed: int = 0) -> dict[str, int]:
    return {
        "attempted": succeeded + partial + failed,
        "succeeded": succeeded,
        "partial": partial,
        "failed": failed,
        "jobs_found": 10,
        "raw_failures": 0,
    }


class _Result:
    month = "2026-11"

    class bronze:  # noqa: N801
        processed = [1, 2]
        bad: list[Any] = []

    reports = [Path("data/reports/2026-11.html")]


def _wire(
    rm: ModuleType, monkeypatch: pytest.MonkeyPatch, summary: dict[str, int] | None
) -> list[str]:
    calls: list[str] = []

    def fake_collect(*args: Any, **kwargs: Any) -> tuple[int, dict[str, int] | None]:
        calls.append("collect")
        return 0, summary

    def fake_monthly(*args: Any, **kwargs: Any) -> Any:
        calls.append("monthly")
        return _Result()

    monkeypatch.setattr(rm, "collect", fake_collect)
    monkeypatch.setattr(rm, "run_monthly", fake_monthly)
    return calls


def test_collect_runs_before_processing(
    rm: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _wire(rm, monkeypatch, _summary(succeeded=3))
    assert rm.main([]) == 0
    assert calls == ["collect", "monthly"]
    out = capsys.readouterr().out
    assert "SUCCEEDED=3" in out and "mes: 2026-11" in out and "2026-11.html" in out


def test_one_failure_does_not_stop_the_run(rm: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _wire(rm, monkeypatch, _summary(succeeded=2, partial=1, failed=1))
    assert rm.main([]) == 0
    assert calls == ["collect", "monthly"]


@pytest.mark.parametrize(
    "summary",
    [_summary(failed=4), _summary(succeeded=1, failed=3), _summary(), None],
)
def test_threshold_blocks_dashboard(
    rm: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    summary: dict[str, int] | None,
) -> None:
    calls = _wire(rm, monkeypatch, summary)
    assert rm.main(["--limit", "4"]) == 1
    assert calls == ["collect"]
    assert "dashboard nao refeito" in capsys.readouterr().err


def test_exactly_half_is_enough(rm: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _wire(rm, monkeypatch, _summary(succeeded=1, partial=1, failed=2))
    assert rm.main([]) == 0
    assert calls == ["collect", "monthly"]


def test_skip_collect_only_processes(
    rm: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _wire(rm, monkeypatch, _summary(succeeded=1))
    assert rm.main(["--skip-collect"]) == 0
    assert calls == ["monthly"]
    assert "pulada" in capsys.readouterr().out


def test_invalid_month_fails_before_collecting(
    rm: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _wire(rm, monkeypatch, _summary(succeeded=1))
    assert rm.main(["--month", "2026-13"]) == 2
    assert calls == []


def test_pipeline_never_touches_hn_history() -> None:
    roots = [
        SCRIPTS / "run_monthly.py",
        SCRIPTS / "collect_local.py",
        *sorted((SCRIPTS.parent / "src" / "vaya_indexer" / "lake").glob("*.py")),
    ]
    for path in roots:
        if path.name in ("history_hn.py", "history_build.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *[a.name for a in node.names]]
            assert not any(
                "history_hn" in n or "load_history_hn" in n or "history_build" in n for n in names
            ), path
    for path in roots[:2]:
        assert "history" not in path.read_text(encoding="utf-8")
