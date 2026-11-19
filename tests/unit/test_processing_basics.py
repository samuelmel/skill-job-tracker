from decimal import Decimal

import pytest

from vaya_indexer.processing.attributes import (
    EmploymentType,
    WorkplaceType,
    infer_workplace_type,
    normalize_employment_type,
)
from vaya_indexer.processing.location import location_relation, normalize_location
from vaya_indexer.processing.salary import SalaryPeriod, parse_salary
from vaya_indexer.processing.text import html_to_text, jaccard, shingles, word_tokens
from vaya_indexer.processing.urls import canonical_url

# ---- localizacao ----


def test_location_structured_and_key() -> None:
    loc = normalize_location("Austin, TX, United States")
    assert (loc.city, loc.region, loc.country, loc.is_remote) == ("austin", "TX", "US", False)
    assert loc.location_key == "austin|tx|us"
    assert loc.raw == "Austin, TX, United States"


def test_location_remote_variants() -> None:
    assert normalize_location("Remote").is_remote
    assert normalize_location("Remote").location_key == "remote"
    us = normalize_location("Remote - US")
    assert us.is_remote and us.country == "US" and us.location_key == "remote|us"
    assert normalize_location("Anywhere").is_remote


def test_location_unknown_text_is_not_invented() -> None:
    loc = normalize_location("Sao Paulo")
    assert (loc.city, loc.region, loc.country) == (None, None, None)
    assert loc.location_key == "sao paulo"
    empty = normalize_location(None)
    assert not empty.is_known and empty.location_key == ""
    assert not normalize_location("   ").is_known


def test_location_ambiguous_two_letter_code_is_not_inferred() -> None:
    assert normalize_location("CA").region is None


def test_location_relation() -> None:
    berlin = normalize_location("Berlin, Germany")
    assert location_relation(berlin, normalize_location("berlin, germany")) == "same"
    assert location_relation(berlin, normalize_location("Munich, Germany")) == "different"
    assert location_relation(berlin, normalize_location(None)) == "unknown"
    assert location_relation(berlin, normalize_location("Remote - Germany")) == "different"
    assert (
        location_relation(normalize_location("Remote - US"), normalize_location("Remote"))
        == "compatible"
    )
    assert (
        location_relation(normalize_location("Remote - US"), normalize_location("Remote - Brazil"))
        == "different"
    )


# ---- workplace / employment ----


@pytest.mark.parametrize(
    ("location", "title", "explicit", "expected"),
    [
        ("Remote - Brazil", "Engineer", None, WorkplaceType.REMOTE),
        ("London", "Engineer", "Hybrid", WorkplaceType.HYBRID),
        ("Remote / Hybrid", "Engineer", None, WorkplaceType.HYBRID),
        ("Paris (On-site)", "Engineer", None, WorkplaceType.ONSITE),
        ("Lisbon", "Engineer (Remote)", None, WorkplaceType.REMOTE),
        ("Lisbon", "Engineer - Remote", None, WorkplaceType.REMOTE),
        ("Lisbon", "Remote Sensing Engineer", None, None),
        (None, None, None, None),
    ],
)
def test_infer_workplace_type(
    location: str | None, title: str | None, explicit: str | None, expected: WorkplaceType | None
) -> None:
    assert infer_workplace_type(location, title, explicit) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Full-time", EmploymentType.FULL_TIME),
        ("Part Time", EmploymentType.PART_TIME),
        ("Contractor", EmploymentType.CONTRACT),
        ("Internship", EmploymentType.INTERNSHIP),
        ("Temporary", EmploymentType.TEMPORARY),
        ("Something odd", EmploymentType.OTHER),
        ("", None),
        (None, None),
    ],
)
def test_normalize_employment_type(raw: str | None, expected: EmploymentType | None) -> None:
    assert normalize_employment_type(raw) is expected


# ---- salario ----


def test_salary_usd_k_range() -> None:
    s = parse_salary("$120k - $150k")
    assert s is not None
    assert (s.min, s.max, s.currency, s.period) == (
        Decimal(120000),
        Decimal(150000),
        "USD",
        SalaryPeriod.YEAR,
    )


def test_salary_code_with_commas_and_period() -> None:
    s = parse_salary("USD 100,000–120,000 per year")
    assert s is not None
    assert (s.min, s.max, s.currency, s.period) == (
        Decimal(100000),
        Decimal(120000),
        "USD",
        SalaryPeriod.YEAR,
    )


def test_salary_euro_with_dot_thousands() -> None:
    s = parse_salary("€50.000 - €60.000 per year")
    assert s is not None
    assert (s.min, s.max, s.currency) == (Decimal(50000), Decimal(60000), "EUR")


def test_salary_hourly_single_value() -> None:
    s = parse_salary("£25/hour")
    assert s is not None
    assert (s.min, s.max, s.currency, s.period) == (
        Decimal(25),
        Decimal(25),
        "GBP",
        SalaryPeriod.HOUR,
    )


def test_salary_open_ended_bounds() -> None:
    up = parse_salary("Up to $90k per year")
    assert up is not None and up.min is None and up.max == Decimal(90000)
    frm = parse_salary("From USD 80,000 per year")
    assert frm is not None and frm.min == Decimal(80000) and frm.max is None


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Competitive salary",
        "100,000 - 120,000 per year",  # sem moeda
        "£100k - EUR 120k",  # moedas conflitantes
        "USD 1.500 - 2.000",  # sem periodo e sem sufixo k
        "$50 per hour or $100k per year",  # periodos conflitantes
        "USD 100 200 300 per year",  # mais de dois valores
        "USD 150,000 - 100,000 per year",  # intervalo invertido
    ],
)
def test_salary_ambiguous_is_none(text: str | None) -> None:
    assert parse_salary(text) is None


# ---- URL e texto ----


def test_canonical_url() -> None:
    assert (
        canonical_url("HTTPS://Boards.Greenhouse.io/acme/jobs/1/?utm_source=x&gh_src=y#apply")
        == "https://boards.greenhouse.io/acme/jobs/1"
    )
    assert (
        canonical_url("https://a.com/jobs?gh_jid=5&ref=z&source=w") == "https://a.com/jobs?gh_jid=5"
    )
    assert canonical_url("https://a.com/x/") == canonical_url("https://A.com/x")


def test_html_to_text_and_shingles() -> None:
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p><script>x()</script>") == "Hello world"
    assert html_to_text(None) == ""
    toks = word_tokens("A b c d")
    assert shingles(toks) == {("a", "b", "c"), ("b", "c", "d")}
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
