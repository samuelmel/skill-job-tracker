"""Normalizacao dos conectores do lote 1 a partir das fixtures sinteticas."""

import json
from decimal import Decimal
from typing import Any

import pytest

from vaya_indexer.connectors.base import NormalizedJob, RawJob
from vaya_indexer.connectors.registry import get_connector_class
from vaya_indexer.coverage.metadata import load_platform_metadata

# (conector, indice da vaga na fixture, campos esperados)
EXPECTED: list[tuple[str, int, dict[str, Any]]] = [
    (
        "ashby",
        0,
        {
            "title": "Backend Engineer",
            "salary_min": Decimal(100000),
            "salary_max": Decimal(140000),
            "salary_currency": "USD",
            "salary_period": "YEAR",
            "employment_type": "FULL_TIME",
            "workplace_type": "REMOTE",
        },
    ),
    ("ashby", 1, {"employment_type": "INTERNSHIP", "salary_min": None}),
    ("lever_co", 0, {"salary_period": "YEAR", "workplace_type": "REMOTE", "salary_max": 120000}),
    ("lever_co", 1, {"salary_min": None, "workplace_type": None, "description": None}),
    ("recruitee", 0, {"workplace_type": "HYBRID", "salary_period": "MONTH", "salary_min": 4000}),
    ("recruitee", 1, {"salary_min": None, "location": None}),
    ("workable", 0, {"workplace_type": "HYBRID", "employment_type": "FULL_TIME"}),
    ("workable", 1, {"employment_type": "PART_TIME", "workplace_type": "REMOTE"}),
    ("breezy_hr", 0, {"company_name": "Exemplo Care", "employment_type": "FULL_TIME"}),
    ("breezy_hr", 1, {"workplace_type": "REMOTE", "employment_type": None}),
    ("bamboohr", 0, {"employment_type": "INTERNSHIP"}),
    ("bamboohr", 1, {"workplace_type": "REMOTE", "employment_type": "FULL_TIME"}),
    ("gem_com", 0, {"workplace_type": "HYBRID", "employment_type": "FULL_TIME"}),
    ("gem_com", 1, {"workplace_type": "REMOTE", "location": None}),
    (
        "pinpoint",
        0,
        {"salary_currency": "GBP", "salary_period": "YEAR", "workplace_type": "REMOTE"},
    ),
    # compensation_visible=False: salario nao e exposto
    ("pinpoint", 1, {"salary_min": None, "employment_type": "INTERNSHIP"}),
    # "No remote" nao pode virar REMOTE
    ("polymer", 0, {"workplace_type": "ONSITE", "salary_period": "YEAR", "salary_min": 170000}),
    ("polymer", 1, {"workplace_type": "REMOTE", "employment_type": "PART_TIME"}),
    ("rippling", 0, {"workplace_type": "REMOTE", "location": "Remote (United States)"}),
    ("rippling", 1, {"location": None, "workplace_type": None}),
    ("hirehive", 0, {"salary_currency": "EUR", "salary_period": "YEAR", "salary_min": 40000}),
    ("hirehive", 1, {"salary_period": "HOUR", "employment_type": "CONTRACT"}),
    ("manatal", 0, {"location": "Windhoek, Namibia", "employment_type": "FULL_TIME"}),
    ("manatal", 1, {"workplace_type": "REMOTE", "location": "Manila, Philippines"}),
    ("workday", 0, {"location": "US, CA, Santa Clara", "description": None}),
    ("workday", 1, {"workplace_type": "REMOTE"}),
]


def _normalize(name: str, index: int) -> NormalizedJob:
    cls = get_connector_class(name)
    assert cls.fixture_path is not None
    raw = json.loads(cls.fixture_path.read_text(encoding="utf-8"))["raw_jobs"][index]
    instances = [
        i.config
        for m in load_platform_metadata().values()
        if m.connector_type == name
        for i in m.example_instances
    ]
    connector = cls.__new__(cls)
    connector.config = cls.validate_config(instances[0])
    return connector.normalize(RawJob.model_validate(raw))


@pytest.mark.parametrize(("name", "index", "expected"), EXPECTED)
def test_normalize_fixture(name: str, index: int, expected: dict[str, Any]) -> None:
    job = _normalize(name, index)
    for field, value in expected.items():
        actual = getattr(job, field)
        actual = getattr(actual, "value", actual)
        assert actual == value, f"{name}[{index}].{field}: {actual!r} != {value!r}"


@pytest.mark.parametrize("name", sorted({e[0] for e in EXPECTED}))
def test_normalize_sets_urls_for_listings_with_link(name: str) -> None:
    job = _normalize(name, 0)
    assert job.original_url
    assert job.original_url.startswith("https://")
