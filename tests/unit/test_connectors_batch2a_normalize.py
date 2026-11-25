"""Normalizacao dos conectores do lote 2A a partir das fixtures sinteticas."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from skilometer.connectors.base import NormalizedJob, RawJob
from skilometer.connectors.registry import get_connector_class
from skilometer.coverage.metadata import load_platform_metadata

EXPECTED: list[tuple[str, int, dict[str, Any]]] = [
    (
        "comeet",
        0,
        {
            "title": "Backend Engineer",
            "company_name": "Exemplo",
            "location": "Tel Aviv, Israel",
            "workplace_type": "REMOTE",
            "employment_type": "FULL_TIME",
            "original_posted_at": None,
        },
    ),
    ("comeet", 1, {"employment_type": "PART_TIME", "location": "Haifa, IL", "description": None}),
    ("applicantpro", 0, {"location": "Richmond, VA", "employment_type": "FULL_TIME"}),
    (
        "applicantpro",
        1,
        {"location": "Austin, TX, USA", "workplace_type": "REMOTE", "salary_min": None},
    ),
    ("careerplug", 0, {"employment_type": "FULL_TIME", "location": "Dallas, TX, US"}),
    ("careerplug", 1, {"employment_type": "PART_TIME", "description": None, "location": None}),
    (
        "eightfold_ai",
        0,
        {
            "workplace_type": "REMOTE",
            "description": None,
            "original_posted_at": datetime.fromtimestamp(1750000000, tz=UTC),
        },
    ),
    ("eightfold_ai", 1, {"workplace_type": "ONSITE", "description": "Texto."}),
    ("oracle_recruiting", 0, {"workplace_type": "REMOTE", "description": "Resumo sintetico."}),
    ("oracle_recruiting", 1, {"original_posted_at": None, "description": None}),
    ("join_com", 0, {"location": "Bristol, United Kingdom", "employment_type": "CONTRACT"}),
    ("join_com", 1, {"workplace_type": "REMOTE", "location": None, "employment_type": None}),
    ("jobvite", 0, {"location": "Camas, Washington", "title": "Field Technician"}),
    ("jobvite", 1, {"location": "Remote", "workplace_type": "REMOTE"}),
    ("jazzhr", 0, {"location": "Remote", "workplace_type": "REMOTE"}),
    ("jazzhr", 1, {"location": None, "description": None}),
    ("icims", 0, {"employment_type": "FULL_TIME", "location": "US-CA-Fresno"}),
    ("icims", 1, {"employment_type": None, "location": None}),
    ("paylocity", 0, {"workplace_type": "REMOTE", "location": "Remote US", "description": None}),
    ("paylocity", 1, {"location": "Portland, OR, USA", "original_posted_at": None}),
    ("avature", 0, {"location": "Argentina", "title": "Backend Software Engineer"}),
    ("avature", 1, {"location": None, "original_posted_at": None}),
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
def test_normalize_sets_urls(name: str) -> None:
    job = _normalize(name, 0)
    assert job.original_url
    assert job.original_url.startswith("https://")
