"""Parse de RSS 2.0 e Atom 1.0 (lxml com entidades/DTD/rede desligados)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from lxml import etree

from vaya_indexer.core.exceptions import ParseError
from vaya_indexer.processing.normalization import normalize_whitespace, parse_iso_datetime

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"


@dataclass(frozen=True)
class FeedEntry:
    id: str
    title: str
    link: str | None = None
    summary: str | None = None
    content: str | None = None
    published: datetime | None = None
    updated: datetime | None = None
    author: str | None = None
    categories: list[str] = field(default_factory=list)


def _text(element: Any, tag: str) -> str | None:
    child = element.find(tag)
    if child is None:
        return None
    return _text_of(child)


def _rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _atom_link(entry: Any) -> str | None:
    links = entry.findall(f"{ATOM}link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return str(link.get("href"))
    return str(links[0].get("href")) if links and links[0].get("href") else None


def _rss_entry(item: Any) -> FeedEntry | None:
    title = _text(item, "title")
    link = _text(item, "link")
    guid = _text(item, "guid") or link
    if not title or not guid:
        return None
    return FeedEntry(
        id=guid,
        title=title,
        link=link,
        summary=_text(item, "description"),
        content=_text(item, f"{CONTENT}encoded"),
        published=_rfc822(_text(item, "pubDate")),
        author=_text(item, "author") or _text(item, f"{DC}creator"),
        categories=[c for c in (_text_of(e) for e in item.findall("category")) if c],
    )


def _text_of(element: Any) -> str | None:
    return normalize_whitespace("".join(element.itertext())) or None


def _atom_entry(entry: Any) -> FeedEntry | None:
    title = _text(entry, f"{ATOM}title")
    link = _atom_link(entry)
    entry_id = _text(entry, f"{ATOM}id") or link
    if not title or not entry_id:
        return None
    author = entry.find(f"{ATOM}author")
    return FeedEntry(
        id=entry_id,
        title=title,
        link=link,
        summary=_text(entry, f"{ATOM}summary"),
        content=_text(entry, f"{ATOM}content"),
        published=parse_iso_datetime(_text(entry, f"{ATOM}published")),
        updated=parse_iso_datetime(_text(entry, f"{ATOM}updated")),
        author=_text(author, f"{ATOM}name") if author is not None else None,
        categories=[str(c.get("term")) for c in entry.findall(f"{ATOM}category") if c.get("term")],
    )


def parse_feed(xml: str | bytes) -> list[FeedEntry]:
    """Entradas de um feed RSS 2.0 ou Atom 1.0. Itens sem titulo ou id sao descartados."""
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=False
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"Feed invalido: {exc}") from exc
    if root.tag == f"{ATOM}feed":
        entries = (_atom_entry(e) for e in root.findall(f"{ATOM}entry"))
    elif root.tag == "rss" or root.tag.endswith("}RDF"):
        entries = (_rss_entry(i) for i in root.iter("item"))
    else:
        raise ParseError(f"Raiz de feed nao reconhecida: {root.tag}")
    return [e for e in entries if e is not None]
