"""Compactacao do warehouse DuckDB.

Cada `dbt build` reescreve tabelas no mesmo arquivo e ele cresce (blocos livres nao voltam ao
disco). Copiar o banco inteiro para um arquivo novo devolve esse espaco. A troca so acontece
depois de validar o arquivo novo; qualquer falha deixa o original intacto.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

_SYSTEM_SCHEMAS = ("information_schema", "pg_catalog")


class CompactError(RuntimeError):
    """O warehouse compactado nao passou na validacao; o original nao foi tocado."""


@dataclass
class CompactResult:
    bytes_before: int
    bytes_after: int
    tables: int
    views: int


def _objects(con: duckdb.DuckDBPyConnection, catalog: str) -> dict[str, str]:
    """`schema.nome` -> BASE TABLE | VIEW, so do catalogo dado."""
    rows = con.execute(
        "select table_schema, table_name, table_type from information_schema.tables "
        "where table_catalog = ? and table_schema not in (?, ?)",
        [catalog, *_SYSTEM_SCHEMAS],
    ).fetchall()
    return {f"{s}.{n}": t for s, n, t in rows}


def _count(con: duckdb.DuckDBPyConnection, catalog: str, obj: str) -> int:
    schema, name = obj.split(".", 1)
    row = con.execute(f'select count(*) from {catalog}."{schema}"."{name}"').fetchone()
    return int(row[0]) if row else -1


def _unlink(path: Path) -> None:
    for p in (path, path.with_name(path.name + ".wal")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            log.warning("nao consegui remover o temporario %s", p)


def compact_warehouse(warehouse: Path | str) -> CompactResult:
    """Reescreve o warehouse sem espaco livre, valida e troca por `os.replace` (atomico).

    Valida a contagem de todas as tabelas e views e a existencia de todas as views. Levanta
    CompactError (ou o erro do DuckDB) se algo divergir; nesse caso o temporario e removido e
    o original permanece como estava. Nenhuma outra conexao ao arquivo pode estar aberta.
    """
    src = Path(warehouse)
    tmp = src.with_name(src.name + ".compact.tmp")
    _unlink(tmp)
    before = src.stat().st_size
    con = duckdb.connect(":memory:")
    try:
        try:
            con.execute(f"attach '{src.as_posix()}' as s (read_only)")
            con.execute(f"attach '{tmp.as_posix()}' as d")
            con.execute("copy from database s to d")
            want, got = _objects(con, "s"), _objects(con, "d")
            missing = sorted(set(want) - set(got))
            if missing:
                raise CompactError(f"objetos ausentes no compactado: {', '.join(missing)}")
            for obj in sorted(want):
                a, b = _count(con, "s", obj), _count(con, "d", obj)
                if a != b:
                    raise CompactError(f"contagem divergente em {obj}: {a} != {b}")
                if want[obj] != got[obj]:
                    raise CompactError(f"tipo divergente em {obj}: {want[obj]} != {got[obj]}")
        finally:
            con.close()  # solta os dois arquivos antes da troca (obrigatorio no Windows)
        os.replace(tmp, src)
    except BaseException:
        _unlink(tmp)
        raise
    n_views = sum(1 for t in want.values() if t == "VIEW")
    return CompactResult(before, src.stat().st_size, len(want) - n_views, n_views)


def compact_quietly(warehouse: Path | str) -> CompactResult | None:
    """Compacta sem nunca propagar erro: o relatorio ja gerado continua valido."""
    try:
        result = compact_warehouse(warehouse)
    except Exception as exc:
        log.error("compactacao do warehouse falhou (original mantido): %s", exc)
        return None
    log.info(
        "warehouse compactado: %.1f MB -> %.1f MB",
        result.bytes_before / 1e6,
        result.bytes_after / 1e6,
    )
    return result
