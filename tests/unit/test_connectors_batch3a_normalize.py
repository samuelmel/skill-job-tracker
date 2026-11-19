"""Normalizacao dos conectores do lote 3A a partir das fixtures sinteticas."""

import json
from decimal import Decimal
from typing import Any

import pytest

from vaya_indexer.connectors.base import NormalizedJob, RawJob
from vaya_indexer.connectors.registry import get_connector_class
from vaya_indexer.coverage.metadata import load_platform_metadata

EXPECTED: list[tuple[str, int, dict[str, Any]]] = [
    ("niceboard", 0, {"salary_min": Decimal(85000)}),
    ("niceboard", 1, {"salary_min": None}),
    ("smartjobboard", 0, {"salary_min": None}),
    ("smartjobboard", 1, {"location": None}),
    ("jobboard-io", 0, {}),
    ("jobboard-io", 1, {}),
    ("wp-job-manager", 0, {}),
    ("wp-job-manager", 1, {}),
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
    assert job.title
    for field, value in expected.items():
        assert getattr(job, field) == value, f"{name}[{index}].{field}"


@pytest.mark.parametrize("name", sorted({e[0] for e in EXPECTED}))
def test_normalize_keeps_source_and_application_urls(name: str) -> None:
    job = _normalize(name, 0)
    assert job.original_url
    assert job.original_url.startswith("https://")
    assert job.application_url


def test_niceboard_application_url_differs_from_page_when_apply_url_present() -> None:
    job = _normalize("niceboard", 0)
    assert job.application_url != job.original_url
    assert job.application_url
    assert job.application_url.startswith("http")
    job2 = _normalize("niceboard", 1)
    assert job2.application_url == job2.original_url
