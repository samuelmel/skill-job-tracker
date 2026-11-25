from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from skilometer.connectors.base import RawJob
from skilometer.connectors.greenhouse import GreenhouseConnector
from skilometer.extraction.http import HttpClient
from tests.fixtures.greenhouse import board_payload


@pytest.fixture
async def connector() -> AsyncIterator[GreenhouseConnector]:
    async with HttpClient(user_agent="test") as http:
        yield GreenhouseConnector(http, board_token="acme")


def _raw(index: int) -> RawJob:
    payload = board_payload()["jobs"][index]
    return RawJob(external_id=str(payload["id"]), payload=payload)


def test_normalize_full_payload(connector: GreenhouseConnector) -> None:
    job = connector.normalize(_raw(0))
    assert job.title == "Senior Backend Engineer"
    assert job.company_name == "Acme Inc."
    assert job.location == "Remote - Brazil"
    assert job.description == "<p>Build &amp; run services.</p>"
    assert job.original_url == "https://boards.greenhouse.io/acme/jobs/1001"
    assert job.original_posted_at is not None
    assert job.original_posted_at.isoformat() == "2024-04-01T09:00:00-04:00"


def test_normalize_never_invents_missing_fields(connector: GreenhouseConnector) -> None:
    job = connector.normalize(_raw(1))  # sem first_published
    assert job.original_posted_at is None
    assert job.salary_min is None and job.salary_max is None and job.salary_currency is None
    assert job.employment_type is None and job.workplace_type is None
    assert connector.normalize(_raw(2)).description is None  # content vazio


def test_normalize_requires_title(connector: GreenhouseConnector) -> None:
    raw = _raw(0)
    raw.payload["title"] = "   "
    with pytest.raises(ValidationError):
        connector.normalize(raw)
