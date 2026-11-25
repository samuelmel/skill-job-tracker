"""Texto plano a partir de HTML e shingles, para comparar descricoes entre fontes."""

import re

from bs4 import BeautifulSoup

from skilometer.processing.normalization import normalize_whitespace

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return normalize_whitespace(soup.get_text(" "))


def word_tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def shingles(tokens: list[str], size: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def jaccard(a: set[tuple[str, ...]] | set[str], b: set[tuple[str, ...]] | set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
