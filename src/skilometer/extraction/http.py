"""Cliente HTTP assincrono com retry, rate limit por dominio e log estruturado."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlparse

import httpx

from skilometer.core.exceptions import HttpStatusError, ParseError, RetryExhausted

logger = logging.getLogger("skilometer.http")

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0


def parse_retry_after(value: str | None) -> float | None:
    """Converte o header Retry-After (segundos ou data HTTP) em segundos."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 20.0,
        max_retries: int = 3,
        per_domain_concurrency: int = 4,
        min_interval: float = 0.0,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True
        )
        self._max_retries = max_retries
        self._concurrency = per_domain_concurrency
        self._min_interval = min_interval
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._rate_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.request("GET", url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise ParseError(f"JSON invalido em {url}: {exc}") from exc

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlparse(url).netloc
        attempts = self._max_retries + 1
        last_error = "desconhecido"
        for attempt in range(1, attempts + 1):
            retry_after: float | None = None
            started = time.monotonic()
            status: int | None = None
            try:
                response = await self._send(host, method, url, **kwargs)
                status = response.status_code
            except httpx.TransportError as exc:  # inclui timeouts e erros de conexao
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if status < 400:
                    self._log(method, url, status, started, attempt)
                    return response
                if status not in RETRYABLE_STATUS:
                    self._log(method, url, status, started, attempt)
                    raise HttpStatusError(url, status)
                last_error = f"HTTP {status}"
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
            self._log(method, url, status, started, attempt, error=last_error)
            if attempt < attempts:
                await self._sleep(self._delay(attempt, retry_after))
        raise RetryExhausted(url, attempts, last_error)

    async def _send(self, host: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        semaphore = self._semaphores.setdefault(host, asyncio.Semaphore(self._concurrency))
        async with semaphore:
            await self._throttle(host)
            return await self._client.request(method, url, **kwargs)

    async def _throttle(self, host: str) -> None:
        if self._min_interval <= 0:
            return
        lock = self._rate_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_request.get(host, 0.0) + self._min_interval - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last_request[host] = time.monotonic()

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, MAX_RETRY_AFTER)
        ceiling = min(self._backoff_max, self._backoff_base * 2.0 ** (attempt - 1))
        jitter: float = random.uniform(0, ceiling / 2)
        return ceiling / 2 + jitter

    @staticmethod
    def _log(
        method: str,
        url: str,
        status: int | None,
        started: float,
        attempt: int,
        error: str | None = None,
    ) -> None:
        logger.info(
            "http_request",
            extra={
                "method": method,
                "url": url,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "attempt": attempt,
                "error": error,
            },
        )
