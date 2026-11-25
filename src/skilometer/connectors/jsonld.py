"""JSON-LD schema.org `JobPosting` a partir de HTML, convertido em NormalizedJob."""

import json
import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

from skilometer.connectors.base import NormalizedJob
from skilometer.core.exceptions import ParseError
from skilometer.processing.attributes import infer_workplace_type, normalize_employment_type
from skilometer.processing.normalization import normalize_whitespace, parse_iso_datetime
from skilometer.processing.text import html_to_text

_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_PERIODS = frozenset({"HOUR", "DAY", "WEEK", "MONTH", "YEAR"})


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        yield from _walk(node.get("@graph"))


def _is_job_posting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "JobPosting" in kinds


def extract_job_postings(html: str) -> list[dict[str, Any]]:
    """Todos os objetos JobPosting em `<script type="application/ld+json">`.

    Blocos com JSON invalido sao ignorados (comum na web); lista vazia se nao ha nenhum.
    """
    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.get_text() or ""
        try:
            data = json.loads(text)
        except ValueError:
            try:  # sites reais emitem quebras de linha cruas dentro de strings
                data = json.loads(text, strict=False)
            except ValueError:
                try:  # e barras invertidas soltas ("a\b" sem escape valido)
                    data = json.loads(_BAD_ESCAPE.sub(r"\\\\", text), strict=False)
                except ValueError:
                    continue
        found.extend(node for node in _walk(data) if _is_job_posting(node))
    return found


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_whitespace(value) or None
    if isinstance(value, dict):
        return _text(value.get("name"))
    return None


def _place(place: Any) -> str | None:
    if not isinstance(place, dict):
        return None
    address = place.get("address")
    if isinstance(address, str):
        return _text(address)
    if isinstance(address, dict):
        keys = ("addressLocality", "addressRegion", "addressCountry")
        joined = ", ".join(p for p in (_text(address.get(k)) for k in keys) if p)
        return joined or None
    return _text(place.get("name"))


def _location(posting: dict[str, Any]) -> str | None:
    raw = posting.get("jobLocation")
    places = raw if isinstance(raw, list) else [raw]
    names = [n for n in (_place(p) for p in places) if n]
    return "; ".join(dict.fromkeys(names)) or None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _salary(posting: dict[str, Any]) -> dict[str, Any]:
    """baseSalary estruturado; sem moeda ou periodo explicitos nao ha como comparar: ignora."""
    base = posting.get("baseSalary")
    if not isinstance(base, dict):
        return {}
    value = base.get("value")
    currency = base.get("currency")
    if not isinstance(value, dict) or not isinstance(currency, str) or len(currency) != 3:
        return {}
    unit = value.get("unitText")
    period = unit.upper() if isinstance(unit, str) and unit.upper() in _PERIODS else None
    low = _decimal(value.get("minValue"))
    high = _decimal(value.get("maxValue"))
    if low is None and high is None:
        low = high = _decimal(value.get("value"))
    if period is None or (low is None and high is None):
        return {}
    return {
        "salary_min": low,
        "salary_max": high,
        "salary_currency": currency.upper(),
        "salary_period": period,
    }


def job_posting_to_normalized(posting: dict[str, Any]) -> NormalizedJob:
    """Converte um JobPosting schema.org; campos ausentes ficam None."""
    title = _text(posting.get("title")) or _text(posting.get("name"))
    if not title:
        raise ParseError("JobPosting sem title")
    description = posting.get("description")
    location = _location(posting)
    telecommute = str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    employment = posting.get("employmentType")
    if isinstance(employment, list):
        employment = employment[0] if employment else None
    employment_norm = normalize_employment_type(
        employment.replace("_", " ") if isinstance(employment, str) else None
    )
    workplace = infer_workplace_type(location, title, "remote" if telecommute else None)
    url = _text(posting.get("url"))
    posted = _text(posting.get("datePosted"))
    return NormalizedJob(
        title=title,
        company_name=_text(posting.get("hiringOrganization")),
        description=html_to_text(description) if isinstance(description, str) else None,
        location=location,
        original_url=url,
        application_url=url,
        original_posted_at=parse_iso_datetime(posted),
        employment_type=employment_norm.value if employment_norm else None,
        workplace_type=workplace.value if workplace else None,
        **_salary(posting),
    )
