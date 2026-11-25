"""Fetcher de paginas renderizadas via Playwright (extra opcional `browser`).

Uso restrito a conectores com `requires_browser = True`, quando a fonte so entrega o conteudo
apos executar JavaScript. Sem stealth nem anti-deteccao: o navegador se identifica pelo
User-Agent configurado e respeita os mesmos limites (timeout) do restante do projeto.
Instalacao: `uv sync --extra browser` e `uv run playwright install chromium`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from skilometer.core.exceptions import BrowserUnavailable, FetchError

logger = logging.getLogger("skilometer.browser")

DEFAULT_USER_AGENT = "Skilometer/0.1 (+https://example.invalid/contact)"
INSTALL_HINT = "instale com `uv sync --extra browser` e `uv run playwright install chromium`"


@dataclass(frozen=True)
class BrowserPage:
    url: str
    status: int | None
    html: str


def _default_factory() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserUnavailable(f"playwright nao instalado: {INSTALL_HINT}") from exc
    return async_playwright()


class BrowserFetcher:
    """Chromium headless, iniciado de forma preguicosa e fechado em `aclose`."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        playwright_factory: Callable[[], Any] = _default_factory,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout * 1000
        self._factory = playwright_factory
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _start(self) -> Any:
        if self._context is not None:
            return self._context
        self._playwright = await self._factory().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            await self.aclose()
            raise BrowserUnavailable(
                f"Chromium nao pode ser iniciado ({type(exc).__name__}): {INSTALL_HINT}"
            ) from exc
        self._context = await self._browser.new_context(user_agent=self._user_agent)
        return self._context

    async def fetch_html(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        wait_for_selector: str | None = None,
    ) -> BrowserPage:
        context = await self._start()
        page = await context.new_page()
        try:
            response = await page.goto(url, wait_until=wait_until, timeout=self._timeout_ms)
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=self._timeout_ms)
            html = await page.content()
            status = response.status if response is not None else None
            if status is not None and status >= 400:
                raise FetchError(f"HTTP {status} para {url} (browser)")
            return BrowserPage(url=page.url, status=status, html=html)
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"Falha no browser para {url}: {type(exc).__name__}: {exc}") from exc
        finally:
            await page.close()

    async def aclose(self) -> None:
        """Fecha pagina/contexto/navegador/driver; seguro chamar varias vezes ou sem uso."""
        for name, closer in (
            ("_context", "close"),
            ("_browser", "close"),
            ("_playwright", "stop"),
        ):
            resource = getattr(self, name)
            setattr(self, name, None)
            if resource is None:
                continue
            try:
                await getattr(resource, closer)()
            except Exception:
                logger.warning("browser_close_failed", extra={"resource": name})
