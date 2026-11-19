import httpx
import pytest
import respx

from tests.fixtures.greenhouse import BOARD_TOKEN, JOBS_URL, board_payload
from vaya_indexer.connectors.greenhouse import GreenhouseConnector
from vaya_indexer.core.exceptions import HttpStatusError, ParseError
from vaya_indexer.extraction.http import HttpClient


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
