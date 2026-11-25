"""Normalizacao basica de texto, datas e fingerprints."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

_PUNCT_RE = re.compile(r"[^\w\s+#]", re.UNICODE)
_COMPANY_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "ltd",
        "limited",
        "gmbh",
        "ag",
        "corp",
        "corporation",
        "plc",
        "bv",
        "sa",
        "srl",
        "ltda",
    }
)


def normalize_whitespace(value: str) -> str:
    """Colapsa qualquer sequencia de espacos/quebras em um espaco e apara as pontas."""
    return " ".join(value.split())


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def normalize_title(title: str) -> str:
    """Minusculas, sem acentos nem pontuacao irrelevante.

    Tokens de nivel como "Sr", "Jr" e "II" sobrevivem (apenas perdem o ponto/caixa).
    '+' e '#' sao mantidos para nao destruir "C++" e "C#".
    """
    return normalize_whitespace(_PUNCT_RE.sub(" ", _fold(title)))


def normalize_company_name(name: str) -> str:
    """Normaliza o nome e remove sufixos societarios finais (Inc, LLC, Ltd, GmbH...)."""
    tokens = normalize_whitespace(_PUNCT_RE.sub(" ", _fold(name))).split()
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Converte ISO 8601 em datetime com timezone.

    Datas sem fuso sao interpretadas como UTC. Valor ausente ou invalido -> None.
    """
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_fingerprint(company: str, title: str, location: str | None) -> str:
    """Identidade aproximada da vaga: empresa + titulo + location_key normalizados."""
    from skilometer.processing.location import normalize_location  # evita import circular

    parts = [
        normalize_company_name(company),
        normalize_title(title),
        normalize_location(location).location_key,
    ]
    return sha256_hex("|".join(parts))
