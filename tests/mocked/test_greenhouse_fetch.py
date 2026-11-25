import httpx
import pytest
import respx

from skilometer.connectors.greenhouse import GreenhouseConnector
from skilometer.core.exceptions import ConnectorConfigError, HttpStatusError, ParseError
from skilometer.extraction.http import HttpClient
from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload


def connector(http: HttpClient) -> GreenhouseConnector:
    return GreenhouseConnector(http, board_token=BOARD_TOKEN)


@respx.mock
async def test_fetch_jobs_complete() -> None:
    route = respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=board_payload()))
    async with HttpClient(user_agent="t") as http:
        result = await connector(http).fetch_jobs()
    assert route.calls[0].request.url.params["content"] == "true"
    assert [j.external_id for j in result.jobs] == ["1001", "1002", "1003"]
    assert result.complete is True
    assert result.jobs[0].payload["title"] == "  Senior   Backend Engineer "  # payload intacto


@respx.mock
async def test_fetch_jobs_incomplete_when_total_mismatch() -> None:
    payload = board_payload()
    payload["meta"]["total"] = 10
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json=payload))
    async with HttpClient(user_agent="t") as http:
        result = await connector(http).fetch_jobs()
    assert result.complete is False
    assert len(result.jobs) == 3


@respx.mock
async def test_fetch_jobs_unexpected_shape() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(ParseError):
            await connector(http).fetch_jobs()


@respx.mock
async def test_fetch_jobs_404_board() -> None:
    respx.get(JOBS_URL).mock(return_value=httpx.Response(404))
    async with HttpClient(user_agent="t") as http:
        with pytest.raises(HttpStatusError):
            await connector(http).fetch_jobs()


@respx.mock
async def test_health_check() -> None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{BOARD_TOKEN}"
    respx.get(url).mock(return_value=httpx.Response(200, json={"name": "Acme"}))
    async with HttpClient(user_agent="t") as http:
        assert await connector(http).health_check() is True
    respx.get(url).mock(return_value=httpx.Response(404))
    async with HttpClient(user_agent="t") as http:
        assert await connector(http).health_check() is False


@respx.mock
async def test_fetch_jobs_uses_custom_api_base_url() -> None:
    url = "http://127.0.0.1:9999/v1/boards/acme/jobs"
    route = respx.get(url).mock(return_value=httpx.Response(200, json=board_payload()))
    async with HttpClient(user_agent="t") as http:
        result = await GreenhouseConnector(
            http, board_token=BOARD_TOKEN, api_base_url="http://127.0.0.1:9999/v1/boards/"
        ).fetch_jobs()
    assert route.called and len(result.jobs) == 3


def test_api_base_url_must_be_http() -> None:
    with pytest.raises(ConnectorConfigError, match="api_base_url"):
        GreenhouseConnector.validate_config({"board_token": "a", "api_base_url": "ftp://x"})


async def test_normalize_extracts_explicit_attributes_and_salary() -> None:
    payload = board_payload()["jobs"][0]
    payload["metadata"] = [
        {"name": "Employment Type", "value": "Full-time"},
        {"name": "Salary Range", "value": "$120k - $150k"},
    ]
    from skilometer.connectors.base import RawJob

    async with HttpClient(user_agent="t") as http:
        job = connector(http).normalize(RawJob(external_id="1001", payload=payload))
    assert job.workplace_type == "REMOTE" and job.employment_type == "FULL_TIME"
    assert (job.salary_currency, job.salary_period) == ("USD", "YEAR")
    assert job.salary_min is not None and int(job.salary_min) == 120000
