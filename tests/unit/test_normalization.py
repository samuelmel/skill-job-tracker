from datetime import UTC, timedelta

from vaya_indexer.processing.normalization import (
    canonical_fingerprint,
    normalize_company_name,
    normalize_title,
    normalize_whitespace,
    parse_iso_datetime,
)


def test_normalize_whitespace() -> None:
    assert normalize_whitespace("  a \n\t b   c ") == "a b c"


def test_normalize_title_lower_punctuation_and_whitespace() -> None:
    assert normalize_title("  Sr.  Software   Engineer (Remote) ") == "sr software engineer remote"


def test_normalize_title_preserves_levels_and_symbols() -> None:
    assert normalize_title("Data Analyst II") == "data analyst ii"
    assert normalize_title("C++ Developer") == "c++ developer"
    assert normalize_title("Engenheiro Sênior") == "engenheiro senior"


def test_normalize_company_name_strips_suffixes() -> None:
    assert normalize_company_name("Acme, Inc.") == "acme"
    assert normalize_company_name("Foo GmbH") == "foo"
    assert normalize_company_name("Bar Holdings Ltd") == "bar holdings"
    assert normalize_company_name("Baz Co LLC") == "baz co"
    assert normalize_company_name("Inc") == "inc"  # nunca esvazia o nome


def test_parse_iso_datetime_with_timezone() -> None:
    parsed = parse_iso_datetime("2024-05-02T10:15:00-04:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=-4)


def test_parse_iso_datetime_z_naive_and_invalid() -> None:
    z = parse_iso_datetime("2024-05-02T10:15:00Z")
    assert z is not None and z.tzinfo is not None and z.utcoffset() == timedelta(0)
    naive = parse_iso_datetime("2024-05-02T10:15:00")
    assert naive is not None and naive.tzinfo == UTC
    assert parse_iso_datetime("not a date") is None
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("  ") is None


def test_canonical_fingerprint_ignores_case_and_suffix() -> None:
    a = canonical_fingerprint("Acme Inc.", "Sr. Engineer", "Remote")
    b = canonical_fingerprint("ACME", "sr engineer", "remote")
    assert a == b
    assert a != canonical_fingerprint("Acme", "Sr. Engineer", "Lisbon")
