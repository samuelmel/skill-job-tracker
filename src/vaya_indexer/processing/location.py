"""Normalizacao conservadora de localizacao: so infere o que o texto sustenta."""

import re
from dataclasses import dataclass

from vaya_indexer.processing.normalization import normalize_title

_REMOTE_RE = re.compile(
    r"\b(?:remote(?:ly)?|anywhere|worldwide|work from home|wfh|home[- ]?office|distributed)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s*(?:,|;|\||\s[-–—]\s|/|\(|\))\s*")

# Nome/alias normalizado -> ISO 3166-1 alpha-2. Tabela pequena de proposito.
_COUNTRIES: dict[str, str] = {
    "united states": "US", "united states of america": "US", "usa": "US", "us": "US",
    "u s": "US", "u s a": "US", "america": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
    "brazil": "BR", "brasil": "BR", "br": "BR",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES", "espana": "ES",
    "portugal": "PT", "italy": "IT", "netherlands": "NL", "the netherlands": "NL",
    "ireland": "IE", "poland": "PL", "sweden": "SE", "norway": "NO", "denmark": "DK",
    "finland": "FI", "switzerland": "CH", "austria": "AT", "belgium": "BE",
    "india": "IN", "japan": "JP", "china": "CN", "singapore": "SG", "australia": "AU",
    "new zealand": "NZ", "mexico": "MX", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "israel": "IL", "south africa": "ZA", "united arab emirates": "AE", "uae": "AE",
}  # fmt: skip

_US_STATES: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}  # fmt: skip
_US_STATE_BY_NAME = {name: abbr for abbr, name in _US_STATES.items()}


@dataclass(frozen=True)
class NormalizedLocation:
    raw: str | None
    city: str | None
    region: str | None
    country: str | None
    is_remote: bool
    location_key: str

    @property
    def is_known(self) -> bool:
        return bool(self.location_key)


def _classify_country(part: str) -> str | None:
    return _COUNTRIES.get(part)


def _classify_region(part: str) -> str | None:
    """Estado dos EUA (abreviacao em 2 letras ou nome completo) -> abreviacao."""
    if len(part) == 2 and part.upper() in _US_STATES:
        return part.upper()
    return _US_STATE_BY_NAME.get(part)


def _geo_parts(text: str) -> list[str]:
    parts = (normalize_title(p) for p in _SPLIT_RE.split(text))
    return [p for p in parts if p]


def _assign(parts: list[str]) -> tuple[str | None, str | None, str | None]:
    """Distribui as partes em (city, region, country) so quando ha evidencia."""
    rest = list(parts)
    country = _classify_country(rest.pop()) if rest and _classify_country(rest[-1]) else None
    region: str | None = None
    if rest and country in (None, "US"):
        candidate = _classify_region(rest[-1])
        # abreviacao de 2 letras sozinha ("CA") e ambigua (California/Canada): nao inferir
        ambiguous = len(rest) == 1 and len(rest[-1]) == 2 and country is None
        if candidate and not ambiguous:
            region, country = candidate, "US"
            rest.pop()
    city = rest[0] if len(rest) == 1 and (country or region) else None
    return city, region, country


def normalize_location(raw: str | None) -> NormalizedLocation:
    """Extrai city/region/country quando inferiveis; o raw nunca e alterado.

    "Remote", "Remote - US", "Anywhere" -> is_remote=True. Texto que nao se encaixa nas tabelas
    pequenas de paises/estados fica sem estrutura (apenas a chave de comparacao textual).
    """
    if raw is None or not raw.strip():
        return NormalizedLocation(raw, None, None, None, False, "")
    is_remote = bool(_REMOTE_RE.search(raw))
    parts = _geo_parts(_REMOTE_RE.sub(" ", raw) if is_remote else raw)
    city, region, country = _assign(parts)
    structured = [p for p in (city, region, country) if p]
    body = "|".join(structured) if structured else " ".join(parts)
    key = ("remote|" + body) if is_remote and body else ("remote" if is_remote else body)
    return NormalizedLocation(raw, city, region, country, is_remote, key.lower())


def _tokens(loc: NormalizedLocation) -> set[str]:
    return set(loc.location_key.replace("|", " ").split()) - {"remote"}


def location_relation(a: NormalizedLocation, b: NormalizedLocation) -> str:
    """Compara duas localizacoes: 'same' | 'compatible' | 'different' | 'unknown'.

    'unknown' = alguma das duas e desconhecida (nao veta). 'different' = ambas conhecidas e
    conflitantes (veto na deduplicacao).
    """
    if not a.is_known or not b.is_known:
        return "unknown"
    if a.location_key == b.location_key:
        return "same"
    if a.is_remote != b.is_remote:
        return "different"
    if a.is_remote:
        if a.country and b.country and a.country != b.country:
            return "different"
        return "compatible"
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and (ta <= tb or tb <= ta):
        return "compatible"
    return "different"
