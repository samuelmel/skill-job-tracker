"""Parsing conservador de salario. Nunca converte moeda nem periodo; ambiguo -> None."""

import enum
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class SalaryPeriod(enum.StrEnum):
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


@dataclass(frozen=True)
class ParsedSalary:
    min: Decimal | None
    max: Decimal | None
    currency: str
    period: SalaryPeriod


# Simbolos -> ISO 4217. "$" sem qualificador e lido como USD (convencao; documentado no README).
_SYMBOLS = (
    ("r$", "BRL"), ("us$", "USD"), ("ca$", "CAD"), ("c$", "CAD"), ("au$", "AUD"), ("a$", "AUD"),
    ("€", "EUR"), ("£", "GBP"), ("$", "USD"),
)  # fmt: skip
_CODES = frozenset(
    "USD EUR GBP BRL CAD AUD NZD CHF JPY INR MXN SEK NOK DKK PLN CZK SGD ZAR "
    "ILS AED ARS CLP COP".split()
)
_CODE_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
_NUMBER_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)\s*(k\b)?", re.IGNORECASE
)
_PERIODS: tuple[tuple[SalaryPeriod, re.Pattern[str]], ...] = tuple(
    (period, re.compile(pattern, re.IGNORECASE))
    for period, pattern in (
        (SalaryPeriod.HOUR, r"(?:/|per\s+|an?\s+)(?:hour|hr|h)\b|\bhourly\b"),
        (SalaryPeriod.DAY, r"(?:/|per\s+|an?\s+)day\b|\bdaily\b"),
        (SalaryPeriod.WEEK, r"(?:/|per\s+|an?\s+)(?:week|wk)\b|\bweekly\b"),
        (SalaryPeriod.MONTH, r"(?:/|per\s+|an?\s+)(?:month|mo)\b|\bmonthly\b"),
        (
            SalaryPeriod.YEAR,
            r"(?:/|per\s+|an?\s+)(?:year|yr|annum|y)\b|\b(?:annual|annually|yearly|p\.a\.)",
        ),
    )
)
_UP_TO_RE = re.compile(r"\b(?:up to|ate|max(?:imum)?)\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\b(?:from|starting at|min(?:imum)?)\b|\d\s*\+", re.IGNORECASE)


def _currencies(text: str) -> set[str]:
    """Moedas explicitas (codigo ou simbolo); "$" puro so vale como USD se nada mais aparecer."""
    strong: set[str] = set()
    weak: set[str] = set()
    remaining = text.lower()
    for symbol, iso in _SYMBOLS:
        if symbol in remaining:
            (weak if symbol == "$" else strong).add(iso)
            remaining = remaining.replace(symbol, " ")  # "r$" nao conta tambem como "$"
    strong.update(m for m in _CODE_RE.findall(text) if m in _CODES)
    return strong or weak


def _to_decimal(token: str, thousands_k: bool) -> Decimal | None:
    seps = [c for c in token if c in ".,"]
    if not seps:
        normalized = token
    elif "." in token and "," in token:
        decimal_sep = token[max(token.rfind("."), token.rfind(",")) :][0]
        thousands_sep = "," if decimal_sep == "." else "."
        normalized = token.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        sep = seps[0]
        tail = token.rsplit(sep, 1)[1]
        is_thousands = len(seps) > 1 or (len(tail) == 3 and not thousands_k)
        normalized = token.replace(sep, "") if is_thousands else token.replace(sep, ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return value * 1000 if thousands_k else value


def _period(text: str) -> SalaryPeriod | None:
    found = {p for p, pattern in _PERIODS if pattern.search(text)}
    return next(iter(found)) if len(found) == 1 else None


def parse_salary(text: str | None) -> ParsedSalary | None:
    """Interpreta "$120k - $150k", "USD 100,000-120,000 per year", "EUR50.000 - 60.000", "25/hour".

    Retorna None quando falta moeda, ha moedas/periodos conflitantes, mais de dois valores ou
    o periodo nao e deduzivel. Sem palavra de periodo, so assume YEAR quando os valores usam o
    sufixo "k" (>= 10k), convencao anual; "1.500 - 2.000" sem periodo e ambiguo.
    """
    if not text or not text.strip():
        return None
    currencies = _currencies(text)
    if len(currencies) != 1:
        return None
    matches = list(_NUMBER_RE.finditer(text))
    if not matches or len(matches) > 2:
        return None
    uses_k = [m.group(2) is not None for m in matches]
    values = [_to_decimal(m.group(1), k) for m, k in zip(matches, uses_k, strict=True)]
    if any(v is None or v <= 0 for v in values):
        return None
    amounts = [v for v in values if v is not None]
    period = _period(text)
    if period is None:
        if not all(uses_k) or min(amounts) < 10_000:
            return None
        period = SalaryPeriod.YEAR
    return _build(text, amounts, currencies.pop(), period)


def _build(
    text: str, amounts: list[Decimal], currency: str, period: SalaryPeriod
) -> ParsedSalary | None:
    if len(amounts) == 2:
        low, high = amounts
        return None if low > high else ParsedSalary(low, high, currency, period)
    value = amounts[0]
    if _UP_TO_RE.search(text):
        return ParsedSalary(None, value, currency, period)
    if _FROM_RE.search(text):
        return ParsedSalary(value, None, currency, period)
    return ParsedSalary(value, value, currency, period)
