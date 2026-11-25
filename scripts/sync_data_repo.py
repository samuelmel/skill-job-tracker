"""Copia so o raw imutavel + manifestos entre o lake local e o repositorio privado de dados.

Uso: uv run python scripts/sync_data_repo.py {export,restore} --repo-dir DIR [--lake-dir data]

- `export`: lake -> DIR. Carga inicial (ou gravacao mensal) do repositorio `skilometer-data`.
- `restore`: DIR -> lake. Devolve o raw ao runner descartavel do CI.

So viajam `failed-months.json` (meses reprovados no gate), `raw/` (coleta mensal) e
`history/hn/raw/` (Hacker News), e nelas so `manifest.json`, `jobs.jsonl.gz` e `posts.jsonl.gz`.
Bronze, warehouse, relatorios e `.pgdata` nunca. Idempotente: copia arquivo ausente ou diferente
(tamanho/sha256), nao apaga nada, nao roda git nem faz push.
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOTS = (Path("raw"), Path("history") / "hn" / "raw")
REGISTRY = Path("failed-months.json")  # meses reprovados no gate (raiz do lake/repositorio)
ALLOWED_NAMES = frozenset({"manifest.json", "jobs.jsonl.gz", "posts.jsonl.gz"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _same(a: Path, b: Path) -> bool:
    return b.is_file() and a.stat().st_size == b.stat().st_size and _sha256(a) == _sha256(b)


def _wanted(rel: Path) -> bool:
    if rel.name not in ALLOWED_NAMES:
        return False
    return not any(part.startswith((".tmp-", ".old-")) for part in rel.parts)


def sync_raw(src: Path, dst: Path) -> tuple[int, int]:
    """Copia raw + manifestos de `src` para `dst`. Devolve (copiados, ja iguais)."""
    src, dst = src.resolve(), dst.resolve()
    if src == dst:
        raise ValueError("origem e destino sao o mesmo diretorio")
    copied = unchanged = 0
    for root in (*RAW_ROOTS, REGISTRY):
        base = src / root
        if root == REGISTRY:
            paths = [base] if base.is_file() else []
        elif base.is_dir():
            paths = sorted(p for p in base.rglob("*") if p.is_file())
        else:
            continue
        for path in paths:
            rel = path.relative_to(src)
            if root != REGISTRY and not _wanted(rel):
                continue
            target = dst / rel
            if _same(path, target):
                unchanged += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".tmp-{target.name}")
            shutil.copyfile(path, tmp)
            tmp.replace(target)
            copied += 1
    return copied, unchanged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=["export", "restore"])
    parser.add_argument("--repo-dir", required=True, help="checkout do repositorio de dados")
    parser.add_argument("--lake-dir", default="data")
    args = parser.parse_args(argv)
    lake = Path(args.lake_dir)
    if not lake.is_absolute():
        lake = ROOT / lake
    repo = Path(args.repo_dir)
    if args.direction == "export":
        src, dst = lake, repo
    else:
        src, dst = repo, lake
    if not src.is_dir():
        print(f"ERRO origem inexistente: {src}", file=sys.stderr)
        return 1
    try:
        copied, unchanged = sync_raw(src, dst)
    except ValueError as exc:
        print(f"ERRO {exc}", file=sys.stderr)
        return 1
    print(f"{args.direction}: {copied} arquivos copiados, {unchanged} ja iguais ({src} -> {dst})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
