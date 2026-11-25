"""Normalizacao dos conectores do lote 3b a partir das fixtures sinteticas."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from skilometer.connectors.base import NormalizedJob, RawJob
from skilometer.connectors.jobboardly import split_title
from skilometer.connectors.registry import get_connector_class
from skilometer.coverage.metadata import load_platform_metadata

EXPECTED: list[tuple[str, int, dict[str, Any]]] = [
    (
        "jobboardly",
        0,
        {
            "title": "Sales Advisor",
            "company_name": "Acme Ltd",
            "location": "Exeter, Devon, United Kingdom",
            "description": "Sell things.",
            "original_url": "https://jobs.example.com/jobs/1001-sales-advisor-abc12345",
            "application_url": "https://jobs.example.com/jobs/1001-sales-advisor-abc12345",
            "original_posted_at": datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        },
    ),
    # titulo com ' - ' preservado; local vem do titulo quando o job: nao informa
    (
        "jobboardly",
        1,
        {
            "title": "Support - Tier 2",
            "company_name": "Globex",
            "location": "Remote",
            "workplace_type": "REMOTE",
            "original_posted_at": None,
        },
    ),
    (
        "jboard-io",
        0,
        {
            "title": "Welder",
            "company_name": "Acme Co",
            "employment_type": "FULL_TIME",
            "original_url": "https://jobs.example.com/jobs/501-welder",
            "application_url": "https://jobs.example.com/jobs/501/apply",
        },
    ),
    (
        "jboard-io",
        1,
        {"employment_type": None, "application_url": "https://jobs.example.com/jobs/502/apply"},
    ),
    # descricao escapada (&lt;h3&gt;) vira texto limpo, sem marcacao
    ("jobiqo", 0, {"company_name": "Clinic", "employment_type": "FULL_TIME"}),
    (
        "jobiqo",
        1,
        {
            "title": "Cook",
            "original_url": "https://jobs.example.com/job/cook-9002",
            "application_url": "https://jobs.example.com/job/cook-9002",
        },
    ),
    # empresa do cartao vence o 'Confidential Employer' do JSON-LD
    (
        "jobboardhq",
        0,
        {
            "company_name": "Newark Schools",
            "original_url": "https://jobs.example.com/job/AB12CD/teacher/Newark/NJ",
        },
    ),
    # sem detalhe: so dados do cartao, data MM/DD/YYYY
    (
        "jobboardhq",
        1,
        {
            "title": "Aide",
            "company_name": "Camden Schools",
            "location": "Camden, NJ",
            "description": None,
            "original_posted_at": datetime(2026, 11, 17, tzinfo=UTC),
        },
    ),
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


def test_jobiqo_description_is_unescaped_text() -> None:
    description = _normalize("jobiqo", 0).description
    assert description is not None
    assert "&lt;" not in description
    assert "<" not in description
    assert "Care for patients." in description


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("A - B - C", ("A", "B", "C")),
        ("Support - Tier 2 - Globex - Remote", ("Support - Tier 2", "Globex", "Remote")),
        ("Only title", ("Only title", None, None)),
        ("A - B", ("A - B", None, None)),
    ],
)
def test_jobboardly_split_title(title: str, expected: tuple[str, str | None, str | None]) -> None:
    assert split_title(title) == expected
