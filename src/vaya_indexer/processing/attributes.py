"""Tipo de contrato e modalidade de trabalho: so com evidencia textual explicita."""

import enum
import re

from vaya_indexer.processing.normalization import normalize_title


class WorkplaceType(enum.StrEnum):
    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"


class EmploymentType(enum.StrEnum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    CONTRACT = "CONTRACT"
    TEMPORARY = "TEMPORARY"
    INTERNSHIP = "INTERNSHIP"
    OTHER = "OTHER"


_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_REMOTE_RE = re.compile(
    r"\b(?:remote(?:ly)?|work from home|wfh|anywhere|worldwide)\b", re.IGNORECASE
)
_ONSITE_RE = re.compile(r"\b(?:on[- ]?site|in[- ]?office|in[- ]?person)\b", re.IGNORECASE)
# No titulo, "Remote"/"Hybrid" so valem como marcador explicito entre colchetes/parenteses ou
# apos separador no fim ("Engineer - Remote"); "Remote Sensing Engineer" nao conta.
_TITLE_MARKER_RE = re.compile(
    r"(?:[\(\[]\s*(?P<a>[^)\]]*)[\)\]])|(?:[-–—,|]\s*(?P<b>[^-–—,|()\[\]]+)$)"
)


def _classify_workplace(text: str) -> WorkplaceType | None:
    # hibrido antes de remoto: "Remote / Hybrid" e HYBRID
    if _HYBRID_RE.search(text):
        return WorkplaceType.HYBRID
    if _REMOTE_RE.search(text):
        return WorkplaceType.REMOTE
    if _ONSITE_RE.search(text):
        return WorkplaceType.ONSITE
    return None


def _title_marker(title: str) -> WorkplaceType | None:
    for match in _TITLE_MARKER_RE.finditer(title):
        found = _classify_workplace(match.group("a") or match.group("b") or "")
        if found:
            return found
    return None


def infer_workplace_type(
    location: str | None, title: str | None, explicit: str | None = None
) -> WorkplaceType | None:
    """REMOTE | HYBRID | ONSITE so com evidencia textual (explicito > local > titulo)."""
    if explicit:
        found = _classify_workplace(explicit)
        if found:
            return found
    if location:
        found = _classify_workplace(location)
        if found:
            return found
    if title:
        return _title_marker(title)
    return None


_EMPLOYMENT_RULES: tuple[tuple[EmploymentType, tuple[str, ...]], ...] = (
    (EmploymentType.INTERNSHIP, ("intern", "internship", "estagio", "apprentice")),
    (EmploymentType.PART_TIME, ("part time", "parttime")),
    (EmploymentType.FULL_TIME, ("full time", "fulltime", "permanent", "regular")),
    (
        EmploymentType.CONTRACT,
        ("contract", "contractor", "freelance", "fixed term", "b2b"),
    ),
    (EmploymentType.TEMPORARY, ("temporary", "temp", "seasonal", "casual")),
)


def normalize_employment_type(raw: str | None) -> EmploymentType | None:
    """Mapeia texto livre; vazio -> None; texto nao reconhecido -> OTHER."""
    if raw is None or not raw.strip():
        return None
    tokens = f" {normalize_title(raw.replace('-', ' '))} "
    for kind, keywords in _EMPLOYMENT_RULES:
        if any(f" {kw} " in tokens for kw in keywords):
            return kind
    return EmploymentType.OTHER
