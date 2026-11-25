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


def parse_salary(text: str | None, assume_annual_from: int | None = None) -> ParsedSalary | None:
    """Interpreta "$120k - $150k", "USD 100,000-120,000 per year", "EUR50.000 - 60.000", "25/hour".

    Retorna None quando falta moeda, ha moedas/periodos conflitantes, mais de dois valores ou
    o periodo nao e deduzivel. Sem palavra de periodo, so assume YEAR quando os valores usam o
    sufixo "k" (>= 10k), convencao anual; "1.500 - 2.000" sem periodo e ambiguo. Com
    `assume_annual_from`, valores sem "k" e sem palavra de periodo tambem viram YEAR quando o
    menor e >= esse piso (ex.: "$120,000 - $150,000"); o padrao None mantem o comportamento.
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
        annual_k = all(uses_k) and min(amounts) >= 10_000
        annual_floor = (
            assume_annual_from is not None
            and min(amounts) >= assume_annual_from
            and not any(pattern.search(text) for _, pattern in _PERIODS)
        )
        if not (annual_k or annual_floor):
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


# --- Salario anual em USD (secao Empregos do dashboard) --------------------------------------
# Regra unica: so entra USD + YEAR, como ponto medio de [min, max] (ou o valor unico), entre
# 20.000 e 1.000.000. Estruturado (salary_*) tem prioridade; a descricao so e lida quando a vaga
# nao traz salario estruturado nenhum. Hora, dia, semana, mes e outras moedas sao descartados,
# nunca convertidos.
ANNUAL_MIN_USD = 20_000
ANNUAL_MAX_USD = 1_000_000
_ANNUAL_FLOOR = 10_000  # sem "k" nem palavra de periodo, so >= 5 digitos contam como anual
_USD = r"(?<![A-Za-z])(?:US\$|\$)\s?"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[kK]\b|\d{5,}"
_RANGE_RE = re.compile(rf"{_USD}(?:{_NUM})\s*(?:-|\u2013|\u2014|to|and)\s*(?:{_USD})?(?:{_NUM})")
_SINGLE_RE = re.compile(
    rf"{_USD}(?:{_NUM})(?=\s*(?:/|per\s+|a\s+)?\s*(?:year|yr|annum|annual|annually|y)\b)",
    re.IGNORECASE,
)
# Periodo que nao e anual, mesmo com valor alto: "per project", "per 6 months", budget, contract.
_NOT_ANNUAL_RE = re.compile(
    r"\bper\s+project\b|\bper\s+\d+\s*(?:months?|weeks?|mo|wks?)\b|\bbudget\b|\bcontract",
    re.IGNORECASE,
)
_HEAD_CHARS = 20
_TAIL_CHARS = 30


def annual_usd_midpoint(
    low: float | Decimal | None,
    high: float | Decimal | None,
    currency: str | None,
    period: str | None,
) -> int | None:
    """Ponto medio anual em USD, ou None se nao for USD+YEAR, faltar ponta ou sair da faixa."""
    if currency != "USD" or period != SalaryPeriod.YEAR or low is None or high is None:
        return None
    lo, hi = float(low), float(high)
    if lo > hi:
        return None
    mid = (lo + hi) / 2
    return round(mid) if ANNUAL_MIN_USD <= mid <= ANNUAL_MAX_USD else None


def _snippet(text: str, start: int, end: int) -> str:
    head = text[max(0, start - _HEAD_CHARS) : start]
    last_digit = max((i for i, c in enumerate(head) if c.isdigit()), default=-1)
    head = head[last_digit + 1 :]
    tail = re.match(rf"[^\d]{{0,{_TAIL_CHARS}}}", text[end:])
    return head + text[start:end] + (tail.group(0) if tail else "")


def annual_usd_from_text(text: str | None) -> int | None:
    """Ponto medio anual em USD achado na descricao, so para faixas/valores explicitos em "$".

    Aceita "$120,000 - $150,000", "$120k-150k" e "$150,000 per year"; descarta trechos com palavra
    de hora/mes/semana/dia, outra moeda ou periodo ambiguo. Varias faixas (por regiao): vale a
    primeira valida. Faixa de um lado so ("up to", "from") nao tem ponto medio e e descartada.
    """
    if not text:
        return None
    ranges = [m.span() for m in _RANGE_RE.finditer(text)]
    # Um valor unico dentro de uma faixa nunca vale: se a faixa foi rejeitada (outra moeda, hora),
    # o limite superior nao pode voltar sozinho como se fosse salario anual em USD.
    singles = [
        m.span()
        for m in _SINGLE_RE.finditer(text)
        if not any(s < m.end() and m.start() < e for s, e in ranges)
    ]
    for start, end in sorted(ranges + singles):
        snippet = _snippet(text, start, end)
        # Janela crua (o trecho para no proximo digito, e cortaria "per 6 months").
        if _NOT_ANNUAL_RE.search(text[max(0, start - _HEAD_CHARS) : end + _TAIL_CHARS]):
            continue
        parsed = parse_salary(snippet, assume_annual_from=_ANNUAL_FLOOR)
        if parsed is None:
            continue
        value = annual_usd_midpoint(parsed.min, parsed.max, parsed.currency, parsed.period)
        if value is not None:
            return value
    return None
