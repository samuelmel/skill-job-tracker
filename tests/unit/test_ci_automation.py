"""Automacao mensal: workflow YAML, sincronizacao com o repo de dados e montagem do site."""

import gzip
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "monthly.yml"


def _script(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    sys.modules.pop(name, None)
    return __import__(name)


@pytest.fixture(scope="module")
def wf() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_triggers_concurrency_and_timeout(wf: dict[str, Any]) -> None:
    on = wf[True] if True in wf else wf["on"]  # PyYAML le `on` como booleano
    assert on["schedule"] == [{"cron": "17 6 1 * *"}]
    assert "workflow_dispatch" in on
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["collect"]["timeout-minutes"] == 340


def test_permissions_are_minimal_and_pages_only_in_deploy(wf: dict[str, Any]) -> None:
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["collect"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert wf["jobs"]["deploy"]["needs"] == "collect"
    assert "if" not in wf["jobs"]["deploy"]  # so o sucesso de `collect` (gate) libera o deploy


def test_actions_pinned_by_full_sha_with_version_comment() -> None:
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", WORKFLOW.read_text("utf-8"), re.M)
    assert len(uses) >= 5
    for ref, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.search(r"#\s*v\d", rest), f"sem versao em comentario: {ref}"


def test_data_repo_and_no_personal_paths_or_email() -> None:
    text = WORKFLOW.read_text("utf-8")
    assert "secrets.DATA_REPO_SSH_KEY" in text
    assert "${{ github.repository_owner }}/skill-job-tracker-data" in text
    assert not re.search(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/Users/", text)
    no_ssh_user = text.replace("git@github.com", "")  # usuario SSH do GitHub, nao e e-mail
    assert not re.search(r"[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[a-z]{2,}", no_ssh_user)
    # So raw + manifesto entram no commit do repo de dados.
    assert "for path in raw history/hn/raw failed-months.json; do" in text
    for forbidden in ("bronze", "warehouse", ".pgdata"):
        assert not re.search(rf"git add[^\n]*{re.escape(forbidden)}", text)


def _steps(wf: dict[str, Any]) -> list[dict[str, Any]]:
    steps = wf["jobs"]["collect"]["steps"]
    assert isinstance(steps, list)
    return steps


def test_deploy_key_is_isolated_in_the_push_step(wf: dict[str, Any]) -> None:
    steps = _steps(wf)
    text = WORKFLOW.read_text("utf-8")
    # Checkout do repo de dados: a chave nao fica gravada depois do clone.
    data_checkout = next(s for s in steps if s.get("with", {}).get("path") == ".data-repo")
    assert data_checkout["with"]["persist-credentials"] is False
    # A chave aparece so no checkout (ssh-key) e no passo final de push.
    holders = [s for s in steps if "DATA_REPO_SSH_KEY" in yaml.safe_dump(s)]
    assert [s["name"] for s in holders] == [
        data_checkout["name"],
        "Enviar commit ao repositorio de dados",
    ]
    push = holders[1]
    run = push["run"]
    assert "ssh-agent" in run and "ssh-add" in run
    assert "StrictHostKeyChecking=yes" in run and "UserKnownHostsFile" in run
    assert "BatchMode=yes" in run
    assert "IdentitiesOnly" not in run  # sem IdentityFile, esconderia a chave do ssh-agent
    assert "ssh-add - > /dev/null 2>&1; then" in run and "exit 1" in run  # falha nao e engolida
    assert "HEAD:refs/heads/main" in run  # checkout deixa HEAD destacado
    assert re.search(r"github\.com ssh-ed25519 AAAA\w+", run)
    assert "core.hooksPath=/dev/null" in run
    # Export, add e commit rodam num passo sem segredos (nem no env nem nos comandos).
    stage = next(s for s in steps if s.get("id") == "stage")
    assert "env" not in stage and "secrets." not in yaml.safe_dump(stage)
    assert "sync_data_repo.py export" in stage["run"]
    assert "core.hooksPath=/dev/null" in stage["run"]
    # Nenhum passo exporta segredos para o job inteiro e nada usa token do GITHUB_TOKEN no push.
    assert "env" not in wf["jobs"]["collect"]
    assert "secrets." not in yaml.safe_dump(wf["env"])
    assert text.count("secrets.DATA_REPO_SSH_KEY") == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git")
def test_push_refspec_from_yaml_updates_main_from_detached_head(
    wf: dict[str, Any], tmp_path: Path
) -> None:
    push = next(s for s in _steps(wf) if s.get("name") == "Enviar commit ao repositorio de dados")
    line = next(ln for ln in push["run"].splitlines() if " push " in ln)
    refspec = line.split()[-1]
    bare = tmp_path / "data.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", str(work)], check=True)

    def git(*args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout.strip()

    (work / "a.txt").write_text("1", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "c")
    git("checkout", "-q", "--detach")  # como o actions/checkout deixa o repo de dados
    git("-c", "core.hooksPath=/dev/null", "push", "-q", str(bare), refspec)
    pushed = subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "refs/heads/main"], capture_output=True, text=True
    )
    assert pushed.stdout.strip() == git("rev-parse", "HEAD")


def test_raw_never_goes_through_actions_artifacts(wf: dict[str, Any]) -> None:
    text = WORKFLOW.read_text("utf-8")
    assert "upload-artifact" not in text and "actions/cache" not in text
    uploads = [s for s in _steps(wf) if "upload-pages-artifact" in s.get("uses", "")]
    assert [u["with"]["path"] for u in uploads] == ["_site"]


def test_failed_month_is_registered_before_the_commit_step(wf: dict[str, Any]) -> None:
    names = [s.get("name") for s in _steps(wf)]
    reg = names.index("Registrar resultado do mes")
    assert names.index("Coletar, reconstruir bronze/warehouse e gerar dashboard") < reg
    assert reg < names.index("Preparar commit do raw no repositorio de dados")
    run = _steps(wf)[reg]["run"]
    assert "failed_months.py mark" in run and "failed_months.py clear" in run


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="bash/git")
@pytest.mark.parametrize("with_raw", [True, False])
def test_stage_step_succeeds_on_first_good_run_without_registry(
    wf: dict[str, Any], tmp_path: Path, with_raw: bool
) -> None:
    """Primeiro run bom: sem registro previo e repo de dados vazio. Roda o `run` do YAML."""
    stage = next(s for s in _steps(wf) if s.get("id") == "stage")
    lines = stage["run"].splitlines()
    script = ROOT / "scripts" / "sync_data_repo.py"
    export = f'"{sys.executable}" "{script}" export --repo-dir .data-repo --lake-dir "$PWD/data"'
    lines = [export if "sync_data_repo.py export" in ln else ln for ln in lines]
    work = tmp_path / "w"
    (work / ".data-repo").mkdir(parents=True)
    git = ["git", "-C", str(work / ".data-repo")]
    subprocess.run([*git, "init", "-q"], check=True)
    if with_raw:
        _raw(work / "data", "raw/p/manifest.json", b"{}")
    _raw(work / "data", "bronze/jobs/x.parquet")  # nunca deve ser staged
    # O passo "Registrar resultado do mes" quando o gate passa:
    from vaya_indexer.lake.failed_months import clear_failed

    clear_failed(work / "data", "2026-11")
    out = work / "out.txt"
    env = {**os.environ, "GITHUB_OUTPUT": str(out), "GITHUB_RUN_ID": "1"}
    res = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", "\n".join(lines)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "changed=true" in out.read_text("utf-8")
    shown = subprocess.run(
        [*git, "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert sorted(shown) == sorted(["failed-months.json", *(["raw/p/manifest.json"] * with_raw)])


def test_sync_carries_the_failed_months_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sd = _script(monkeypatch, "sync_data_repo")
    lake, repo = tmp_path / "lake", tmp_path / "repo"
    _raw(lake, "failed-months.json", b'{"failed_months": ["2026-11"]}')
    assert sd.main(["export", "--repo-dir", str(repo), "--lake-dir", str(lake)]) == 0
    assert (repo / "failed-months.json").read_bytes() == b'{"failed_months": ["2026-11"]}'
    other = tmp_path / "other"
    assert sd.main(["restore", "--repo-dir", str(repo), "--lake-dir", str(other)]) == 0
    assert (other / "failed-months.json").is_file()


def _raw(lake: Path, rel: str, data: bytes = b"x") -> None:
    path = lake / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_sync_copies_only_raw_and_manifest_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sd = _script(monkeypatch, "sync_data_repo")
    lake, repo = tmp_path / "lake", tmp_path / "repo"
    _raw(lake, "raw/platform=a/source=s/dt=2026-11-01/run_id=r/jobs.jsonl.gz", b"jobs")
    _raw(lake, "raw/platform=a/source=s/dt=2026-11-01/run_id=r/manifest.json", b"{}")
    _raw(lake, "history/hn/raw/month=2021-03/thread=1/posts.jsonl.gz", gzip.compress(b"p"))
    _raw(lake, "history/hn/raw/month=2021-03/thread=1/manifest.json", b"{}")
    _raw(lake, "raw/platform=a/.tmp-x/jobs.jsonl.gz")
    _raw(lake, "bronze/jobs/m/x.parquet")
    _raw(lake, "history/hn/bronze/posts/m/x.parquet")
    _raw(lake, "warehouse.duckdb")
    _raw(lake, "reports/index.html")
    assert sd.main(["export", "--repo-dir", str(repo), "--lake-dir", str(lake)]) == 0
    sent = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file())
    assert len(sent) == 4
    assert all(p.startswith(("raw/", "history/hn/raw/")) for p in sent)
    assert not any(".tmp-" in p or "parquet" in p or "duckdb" in p for p in sent)
    assert sd.sync_raw(lake, repo) == (0, 4)  # segunda vez: nada a copiar
    # restore em outro lake devolve exatamente os mesmos 4 arquivos
    other = tmp_path / "other"
    assert sd.main(["restore", "--repo-dir", str(repo), "--lake-dir", str(other)]) == 0
    assert sorted(p.relative_to(other).as_posix() for p in other.rglob("*") if p.is_file()) == sent


def test_sync_rewrites_changed_file_and_rejects_same_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sd = _script(monkeypatch, "sync_data_repo")
    lake, repo = tmp_path / "lake", tmp_path / "repo"
    _raw(lake, "raw/p/manifest.json", b"new")
    _raw(repo, "raw/p/manifest.json", b"old")
    assert sd.sync_raw(lake, repo) == (1, 0)
    assert (repo / "raw/p/manifest.json").read_bytes() == b"new"
    with pytest.raises(ValueError):
        sd.sync_raw(lake, lake)


def _reports(d: Path, extra: str = "") -> None:
    d.mkdir(parents=True)
    (d / "index.html").write_text(f"<html>ok {extra}</html>", encoding="utf-8")
    (d / "dashboard-2026-11.html").write_text("<html>ok</html>", encoding="utf-8")
    (d / "dashboard-draft.html").write_text("<html>rascunho</html>", encoding="utf-8")
    (d / "warehouse.duckdb").write_bytes(b"db")
    (d / "notas.json").write_text("{}", encoding="utf-8")


def test_site_has_only_dashboard_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ps = _script(monkeypatch, "prepare_site")
    _reports(tmp_path / "reports", "http://exemplo.org/a")
    out = tmp_path / "site"
    assert ps.main(["--reports", str(tmp_path / "reports"), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["dashboard-2026-11.html", "index.html"]


@pytest.mark.parametrize(
    "leak",
    [
        r"C:\Users\fulano\Vaya\data",
        "D:/Downloads/x",
        r"E:\\Dados\\x",  # barra invertida escapada em JSON
        "/github/workspace/data",
        "veja raw/month=2026-11",
        "/home/runner/work/x",
        "/Users/fulano/x",
        "data/warehouse.duckdb",
        "bronze/runs/x.parquet",
        "raw/jobs.jsonl.gz",
        ".pgdata-lake",
    ],
)
def test_site_blocks_local_paths_and_data_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leak: str
) -> None:
    ps = _script(monkeypatch, "prepare_site")
    _reports(tmp_path / "reports", leak)
    out = tmp_path / "site"
    assert ps.main(["--reports", str(tmp_path / "reports"), "--out", str(out)]) == 1
    assert not out.exists()


def test_site_requires_index_and_a_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps = _script(monkeypatch, "prepare_site")
    empty = tmp_path / "reports"
    empty.mkdir()
    assert ps.main(["--reports", str(empty), "--out", str(tmp_path / "s")]) == 1
    (empty / "index.html").write_text("<html></html>", encoding="utf-8")
    assert ps.main(["--reports", str(empty), "--out", str(tmp_path / "s")]) == 1
