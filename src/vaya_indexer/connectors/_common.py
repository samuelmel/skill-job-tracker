"""Utilitarios compartilhados pelos conectores de ATS com API JSON publica."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from vaya_indexer.core.exceptions import ParseError
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.processing.attributes import infer_workplace_type, normalize_employment_type
from vaya_indexer.processing.normalization import normalize_whitespace
from vaya_indexer.processing.salary import ParsedSalary, SalaryPeriod

# Slug de empresa/subdominio: so caracteres seguros para compor host ou caminho (sem SSRF).
SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
SUBDOMAIN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]*$"


def clean(value: Any) -> str | None:
    """Texto curto normalizado (espacos colapsados); vazio ou nao-texto -> None."""
    if not isinstance(value, str):
        return None
    cleaned = normalize_whitespace(value)
    return cleaned or None


def raw_text(value: Any) -> str | None:
    """Texto/HTML longo como veio (so descarta vazio); nao-texto -> None."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def join_parts(*parts: Any, sep: str = ", ") -> str | None:
    """Junta partes textuais nao vazias e sem repeticao, preservando a ordem."""
    seen: list[str] = []
    for part in parts:
        text = clean(part)
        if text and text not in seen:
            seen.append(text)
    return sep.join(seen) or None


def employment(raw: Any) -> str | None:
    text = clean(raw.replace("_", " ")) if isinstance(raw, str) else None
    kind = normalize_employment_type(text)
    return kind.value if kind else None


def workplace(location: str | None, title: str | None, explicit: str | None = None) -> str | None:
    kind = infer_workplace_type(location, title, explicit.replace("_", " ") if explicit else None)
    return kind.value if kind else None


def ms_to_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


_PERIOD_KEYWORDS = (
    (SalaryPeriod.HOUR, ("hour",)),
    (SalaryPeriod.DAY, ("day", "daily")),
    (SalaryPeriod.WEEK, ("week",)),
    (SalaryPeriod.MONTH, ("month",)),
    (SalaryPeriod.YEAR, ("year", "annual", "annum")),
)


def salary_period(raw: Any) -> SalaryPeriod | None:
    """Periodo a partir de rotulo explicito da fonte ('1 YEAR', 'per-month-salary', ...)."""
    if not isinstance(raw, str):
        return None
    text = raw.casefold()
    for period, keywords in _PERIOD_KEYWORDS:
        if any(k in text for k in keywords):
            return period
    return None


def salary_fields(low: Any, high: Any, currency: Any, period: Any) -> dict[str, Any]:
    """Campos de salario do NormalizedJob a partir de valores estruturados da fonte.

    So preenche se houver ao menos um valor, moeda ISO de 3 letras e periodo reconhecido.
    """
    minimum, maximum = to_decimal(low), to_decimal(high)
    parsed_period = salary_period(period)
    code = currency.strip().upper() if isinstance(currency, str) else ""
    if (minimum is None and maximum is None) or len(code) != 3 or parsed_period is None:
        return {}
    return {
        "salary_min": minimum,
        "salary_max": maximum,
        "salary_currency": code,
        "salary_period": parsed_period.value,
    }


def parsed_salary_fields(parsed: ParsedSalary | None) -> dict[str, Any]:
    if parsed is None:
        return {}
    return {
        "salary_min": parsed.min,
        "salary_max": parsed.max,
        "salary_currency": parsed.currency,
        "salary_period": parsed.period.value,
    }


async def request_json(http: HttpClient, method: str, url: str, **kwargs: Any) -> Any:
    """Requisicao pelo HttpClient (retry/rate limit/UA) devolvendo o corpo JSON."""
    response = await http.request(method, url, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise ParseError(f"JSON invalido em {url}: {exc}") from exc
