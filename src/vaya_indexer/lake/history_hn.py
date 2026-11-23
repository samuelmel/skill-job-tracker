"""Carga historica mensal das threads "Ask HN: Who is hiring?" (Hacker News).

Fontes publicas: Algolia HN Search (descoberta das threads e arvore de comentarios) e API
oficial Firebase (conferencia da contagem de comentarios de topo, `kids`).

Layout: <lake_dir>/history/hn/raw/month=YYYY-MM/thread=<id>/{posts.jsonl.gz,manifest.json}

Privacidade: o autor do comentario nunca e gravado; e-mails e telefones sao trocados por
`[email]` / `[phone]` antes de qualquer escrita em disco. Guardamos so post_id, thread_id,
month, created_at e o texto limpo.

Escrita atomica: o diretorio da thread e montado num diretorio temporario irmao e publicado
com rename. Para substituir uma thread existente (mes mais novo, ainda crescendo, ou thread
incompleta) o diretorio antigo e renomeado para `.old-*`, o novo assume o lugar e o antigo e
removido; `_recover` restaura o `.old-*` se o processo morrer entre os dois renames.
"""

import gzip
import hashlib
import html
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from vaya_indexer.core.exceptions import FetchError

logger = logging.getLogger("vaya.history_hn")

SCHEMA_VERSION = 1
SOURCE = "hn_algolia+firebase"
POSTS_FILE = "posts.jsonl.gz"
MANIFEST_FILE = "manifest.json"
AUTHOR = "whoishiring"
ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
FIREBASE_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
USER_AGENT = "vaya-indexer-history/0.1 (historical research load; respects robots.txt)"

_MONTHS = {
    name: i
    for i, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}
_TITLE = re.compile(
    r"^\s*Ask HN:\s*Who is hiring\?\s*\(\s*([A-Za-z]+)\s+(\d{4})\s*\)\s*$", re.IGNORECASE
)


class Fetcher(Protocol):
    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any: ...


def parse_month(title: str) -> str | None:
    """`Ask HN: Who is hiring? (March 2019)` -> `2019-03`; qualquer outro titulo -> None."""
    match = _TITLE.match(title)
    if not match:
        return None
    number = _MONTHS.get(match.group(1).lower())
    if number is None:
        return None
    return f"{int(match.group(2)):04d}-{number:02d}"


# ---------------------------------------------------------------------------- limpeza de texto

# amplo de proposito: pega tambem e-mails truncados/ofuscados (`a@clou...`, `x@_.com`)
_EMAIL = re.compile(r"[\w.%+-]+@[\w.-]+")
_OBF_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+\s*[\[(]\s*at\s*[\])]\s*[A-Za-z0-9-]+"
    r"(?:\s*[\[(]\s*dot\s*[\])]\s*[A-Za-z0-9-]+)+",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<![\w/.@-])(?:\+\s?)?(?:\(\s*\d{1,4}\s*\)\s*)?\d[\d\s().-]{5,}\d(?![\w@])")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RANGE = re.compile(r"^\d{4,}\s*-\s*\d{4,}$")
_HREF = re.compile(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_BREAK = re.compile(r"<\s*(?:p|br|/p|li|/li|pre|/pre)\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def _redact_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = sum(c.isdigit() for c in raw)
    if _ISO_DATE.match(raw.strip()) or _RANGE.match(raw.strip()):
        return raw
    plus = raw.lstrip().startswith("+")
    if digits < 7 or digits > 15:
        return raw
    if not plus and digits < 9 and not re.search(r"[\s().-]", raw.strip()):
        return raw
    # preserva espacos finais que a regex possa ter consumido
    trailing = raw[len(raw.rstrip()) :]
    return "[phone]" + trailing


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _OBF_EMAIL.sub("[email]", text)
    return _PHONE.sub(_redact_phone, text)


def clean_text(raw: str | None) -> str:
    """HTML do HN -> texto simples com e-mails/telefones redigidos. Vazio se nao sobrar nada."""
    if not raw:
        return ""

    def link(match: re.Match[str]) -> str:
        href, label = match.group(1), _TAG.sub("", match.group(2))
        return href if label.rstrip().endswith(("...", "…")) or not label.strip() else label

    text = _HREF.sub(link, raw)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text).strip()
    return redact(text)


# ---------------------------------------------------------------------------------- descoberta


@dataclass(frozen=True)
class Thread:
    thread_id: int
    title: str
    month: str
    created_at: str


@dataclass
class Discovery:
    threads: list[Thread]
    skipped_titles: list[str] = field(default_factory=list)
    duplicate_months: dict[str, list[int]] = field(default_factory=dict)


def select_threads(hits: Iterable[dict[str, Any]]) -> Discovery:
    """Filtra hits do Algolia: so autor whoishiring e titulo exato `Who is hiring? (Mes AAAA)`."""
    found: list[Thread] = []
    skipped: list[str] = []
    for hit in hits:
        title = str(hit.get("title") or "")
        if hit.get("author") != AUTHOR:
            continue
        month = parse_month(title)
        if month is None:
            if "hiring" in title.lower() and "hired" not in title.lower():
                skipped.append(title)
            continue
        found.append(Thread(int(hit["objectID"]), title.strip(), month, str(hit["created_at"])))
    found.sort(key=lambda t: (t.month, t.thread_id))
    by_month: dict[str, list[int]] = {}
    for thread in found:
        by_month.setdefault(thread.month, []).append(thread.thread_id)
    dups = {m: ids for m, ids in by_month.items() if len(ids) > 1}
    return Discovery(found, skipped, dups)


async def discover_threads(http: Fetcher) -> Discovery:
    hits: list[dict[str, Any]] = []
    page = 0
    while True:
        data = await http.get_json(
            ALGOLIA_SEARCH,
            params={
                "tags": f"story,author_{AUTHOR}",
                "hitsPerPage": 1000,
                "page": page,
            },
        )
        hits.extend(data.get("hits", []))
        page += 1
        if page >= int(data.get("nbPages", 1)):
            break
    return select_threads(hits)


def missing_months(months: Iterable[str]) -> list[str]:
    """Meses ausentes entre o primeiro e o ultimo da lista."""
    present = sorted(set(months))
    if not present:
        return []
    have = set(present)
    year, mon = map(int, present[0].split("-"))
    last = tuple(map(int, present[-1].split("-")))
    gaps: list[str] = []
    while (year, mon) <= last:
        key = f"{year:04d}-{mon:02d}"
        if key not in have:
            gaps.append(key)
        year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return gaps


# ------------------------------------------------------------------------------ coleta da thread


@dataclass
class ThreadResult:
    thread: Thread
    posts: list[dict[str, Any]]
    api_top_level_count: int
    firebase_kids_count: int | None
    excluded_deleted_or_empty: int
    complete: bool
    reasons: list[str]


def _iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(thread: Thread, post_id: int, created_at: str, text: str) -> dict[str, Any]:
    return {
        "post_id": post_id,
        "thread_id": thread.thread_id,
        "month": thread.month,
        "created_at": created_at,
        "text": text,
    }


async def fetch_thread(http: Fetcher, thread: Thread) -> ThreadResult:
    reasons: list[str] = []
    posts: dict[int, dict[str, Any]] = {}
    excluded = 0

    tree = await http.get_json(ALGOLIA_ITEM.format(id=thread.thread_id))
    children = [
        c
        for c in (tree or {}).get("children", [])
        if c.get("type") == "comment" and c.get("parent_id") == thread.thread_id
    ]
    api_ids = {int(c["id"]) for c in children}
    for child in children:
        text = clean_text(child.get("text"))
        if not text:
            excluded += 1
            continue
        post_id = int(child["id"])
        posts[post_id] = _post(thread, post_id, _iso(str(child["created_at"])), text)

    kids_count: int | None = None
    item = await http.get_json(FIREBASE_ITEM.format(id=thread.thread_id))
    if not isinstance(item, dict):
        reasons.append("firebase_item_missing")
    else:
        kids = [int(k) for k in item.get("kids", [])]
        kids_count = len(kids)
        extra = api_ids - set(kids)
        if extra:
            reasons.append(f"algolia_not_in_firebase_kids:{len(extra)}")
        for kid in (k for k in kids if k not in api_ids):
            try:
                node = await http.get_json(FIREBASE_ITEM.format(id=kid))
            except FetchError as exc:
                reasons.append(f"firebase_kid_fetch_failed:{kid}:{type(exc).__name__}")
                continue
            if not isinstance(node, dict):
                reasons.append(f"firebase_kid_null:{kid}")
                continue
            text = "" if node.get("deleted") or node.get("dead") else clean_text(node.get("text"))
            if not text:
                excluded += 1
                continue
            created = datetime.fromtimestamp(int(node["time"]), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            posts[kid] = _post(thread, kid, created, text)
    ordered = [posts[k] for k in sorted(posts)]
    return ThreadResult(
        thread=thread,
        posts=ordered,
        api_top_level_count=len(children),
        firebase_kids_count=kids_count,
        excluded_deleted_or_empty=excluded,
        complete=not reasons,
        reasons=reasons,
    )


# ------------------------------------------------------------------------------------- escrita


def month_dir(lake_dir: Path, thread: Thread) -> Path:
    return (
        lake_dir / "history" / "hn" / "raw" / f"month={thread.month}" / f"thread={thread.thread_id}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _recover(final: Path) -> None:
    """Se uma troca foi interrompida (final ausente, `.old-*` presente), restaura o antigo."""
    if final.exists() or not final.parent.exists():
        return
    olds = sorted(final.parent.glob(f".old-{final.name}-*"))
    if olds:
        os.rename(olds[0], final)


def write_thread(lake_dir: Path, result: ThreadResult, fetched_at: datetime) -> Path:
    final = month_dir(lake_dir, result.thread)
    final.parent.mkdir(parents=True, exist_ok=True)
    _recover(final)
    tmp = final.parent / f".tmp-{final.name}-{uuid.uuid4().hex}"
    tmp.mkdir()
    try:
        posts_path = tmp / POSTS_FILE
        with (
            posts_path.open("wb") as raw_fh,
            gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as gz,
        ):
            for post in result.posts:
                gz.write((json.dumps(post, ensure_ascii=False) + "\n").encode("utf-8"))
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE,
            "thread_id": result.thread.thread_id,
            "title": result.thread.title,
            "month": result.thread.month,
            "thread_created_at": _iso(result.thread.created_at),
            "fetched_at": fetched_at.astimezone(UTC).isoformat(),
            "post_count": len(result.posts),
            "api_top_level_count": result.api_top_level_count,
            "firebase_kids_count": result.firebase_kids_count,
            "excluded_deleted_or_empty": result.excluded_deleted_or_empty,
            "posts_file": POSTS_FILE,
            "sha256": _sha256(posts_path),
            "complete": result.complete,
            "incomplete_reasons": result.reasons,
        }
        (tmp / MANIFEST_FILE).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        old: Path | None = None
        if final.exists():
            old = final.parent / f".old-{final.name}-{uuid.uuid4().hex}"
            os.rename(final, old)
        os.rename(tmp, final)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


# ------------------------------------------------------------------------------------ orquestra


@dataclass
class LoadSummary:
    discovered: list[str] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)
    skipped_complete: list[str] = field(default_factory=list)
    incomplete: dict[str, list[str]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    missing_months: list[str] = field(default_factory=list)
    duplicate_months: dict[str, list[int]] = field(default_factory=dict)
    skipped_titles: list[str] = field(default_factory=list)
    total_posts: int = 0


def _in_range(month: str, start: str | None, end: str | None) -> bool:
    return (start is None or month >= start) and (end is None or month <= end)


async def load_history(
    http: Fetcher,
    lake_dir: Path | str,
    *,
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
) -> LoadSummary:
    """Carrega os meses no intervalo. Pula meses ja completos, exceto o mais novo (cresce)."""
    lake = Path(lake_dir)
    discovery = await discover_threads(http)
    summary = LoadSummary(
        discovered=[t.month for t in discovery.threads],
        missing_months=missing_months(t.month for t in discovery.threads),
        duplicate_months=discovery.duplicate_months,
        skipped_titles=discovery.skipped_titles,
    )
    selected = [t for t in discovery.threads if _in_range(t.month, start, end)]
    newest = max((t.month for t in selected), default=None)
    for thread in selected:
        directory = month_dir(lake, thread)
        _recover(directory)
        manifest = read_manifest(directory)
        if manifest and manifest.get("complete") and thread.month != newest:
            summary.skipped_complete.append(thread.month)
            summary.total_posts += int(manifest.get("post_count", 0))
            continue
        try:
            result = await fetch_thread(http, thread)
        except FetchError as exc:
            summary.failed[thread.month] = f"{type(exc).__name__}: {exc}"
            logger.warning("hn_thread_failed", extra={"month": thread.month, "error": str(exc)})
            continue
        write_thread(lake, result, now or datetime.now(UTC))
        summary.loaded.append(thread.month)
        summary.total_posts += len(result.posts)
        if not result.complete:
            summary.incomplete[thread.month] = result.reasons
    return summary
