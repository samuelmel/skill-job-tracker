"""Normalizacao dos conectores do lote 2B a partir das fixtures sinteticas."""

import json
from decimal import Decimal
from typing import Any

import pytest

from skilometer.connectors.base import NormalizedJob, RawJob
from skilometer.connectors.registry import get_connector_class
from skilometer.coverage.metadata import load_platform_metadata

# (conector, indice da vaga na fixture, campos esperados)
EXPECTED: list[tuple[str, int, dict[str, Any]]] = [
    (
        "personio",
        0,
        {
            "title": "Backend Engineer",
            "company_name": "Acme GmbH",
            "location": "Berlin; Munich",
            "employment_type": "FULL_TIME",
            "original_url": "https://personio.jobs.personio.de/job/1001",
        },
    ),
    ("personio", 1, {"employment_type": "INTERNSHIP", "description": None, "company_name": None}),
    ("pageup", 0, {"employment_type": "FULL_TIME", "location": "Cardiff"}),
    ("pageup", 1, {"employment_type": "TEMPORARY", "location": None}),
    (
        "hireology",
        0,
        {
            "salary_min": Decimal(22),
            "salary_currency": "USD",
            "salary_period": "HOUR",
            "description": None,
        },
    ),
    ("hireology", 1, {"workplace_type": "REMOTE", "salary_min": None}),
    ("hiringthing", 0, {"location": "Durham, NC", "employment_type": None}),
    ("hiringthing", 1, {"workplace_type": "REMOTE", "description": None}),
    ("isolvedhire", 0, {"location": "Austin, TX, US", "employment_type": "FULL_TIME"}),
    ("isolvedhire", 1, {"employment_type": "PART_TIME", "salary_min": None}),
    (
        "teamtailor",
        0,
        {"workplace_type": "REMOTE", "location": "Berlin, Germany; Hamburg, Germany"},
    ),
    ("teamtailor", 1, {"workplace_type": None, "location": None}),
    ("phenom-people", 0, {"workplace_type": "REMOTE", "employment_type": "FULL_TIME"}),
    ("phenom-people", 1, {"workplace_type": "ONSITE", "original_url": None}),
    ("zoho-recruit", 0, {"workplace_type": "REMOTE", "employment_type": "FULL_TIME"}),
    # State "N/A" nao entra no local
    ("zoho-recruit", 1, {"location": "Noida", "employment_type": "CONTRACT"}),
    ("trakstar", 0, {"description": "Draft & design.", "company_name": "Acme Engineering"}),
    ("trakstar", 1, {"description": None, "location": None}),
    ("trinet", 0, {"workplace_type": "REMOTE", "employment_type": "FULL_TIME"}),
    ("trinet", 1, {"employment_type": "PART_TIME", "description": None}),
    ("recooty", 0, {"workplace_type": "REMOTE", "location": "Hoffman Estates, IL, US"}),
    ("recooty", 1, {"employment_type": "PART_TIME", "description": None}),
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
