import httpx
import pytest
import respx

from skilometer.core.exceptions import HttpStatusError, RetryExhausted
from skilometer.extraction.http import HttpClient, parse_retry_after

URL = "https://example.test/api"


class Sleeps:
    """Substitui asyncio.sleep: registra os atrasos sem esperar."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


def make_client(sleeps: Sleeps, **kwargs: float) -> HttpClient:
    return HttpClient(user_agent="skilometer-test/1.0", max_retries=3, sleep=sleeps, **kwargs)


@respx.mock
async def test_retries_503_then_succeeds(sleeps: Sleeps) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    async with make_client(sleeps) as client:
        assert await client.get_json(URL) == {"ok": 1}
    assert route.call_count == 3
    assert len(sleeps.calls) == 2
    assert route.calls[0].request.headers["user-agent"] == "skilometer-test/1.0"


@respx.mock
async def test_429_respects_retry_after(sleeps: Sleeps) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={}),
        ]
    )
    async with make_client(sleeps) as client:
        await client.get_json(URL)
    assert route.call_count == 2
    assert sleeps.calls == [7.0]


@respx.mock
async def test_404_is_not_retried(sleeps: Sleeps) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with make_client(sleeps) as client:
        with pytest.raises(HttpStatusError) as info:
            await client.get_json(URL)
    assert info.value.status_code == 404
    assert route.call_count == 1
    assert sleeps.calls == []


@respx.mock
async def test_timeout_exhausts_retries(sleeps: Sleeps) -> None:
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("lento"))
    async with make_client(sleeps) as client:
        with pytest.raises(RetryExhausted) as info:
            await client.get_json(URL)
    assert route.call_count == 4  # 1 tentativa + 3 retries
    assert info.value.attempts == 4
    assert "ReadTimeout" in info.value.last_error


@respx.mock
async def test_persistent_503_raises_retry_exhausted(sleeps: Sleeps) -> None:
    respx.get(URL).mock(return_value=httpx.Response(503))
    async with make_client(sleeps) as client:
        with pytest.raises(RetryExhausted):
            await client.get_json(URL)


@respx.mock
async def test_min_interval_spaces_requests_per_domain(sleeps: Sleeps) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    async with make_client(sleeps, min_interval=5.0) as client:
        await client.get_json(URL)
        await client.get_json(URL)
    assert len(sleeps.calls) == 1
    assert 0 < sleeps.calls[0] <= 5.0


def test_parse_retry_after() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("lixo") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
