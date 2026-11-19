"""Excecoes tipadas do dominio."""


class VayaError(Exception):
    """Base de todas as excecoes do projeto."""


class FetchError(VayaError):
    """Falha ao buscar dados de uma fonte externa."""


class HttpStatusError(FetchError):
    """Resposta HTTP com status de erro nao retentavel."""

    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(f"HTTP {status_code} para {url}")
        self.url = url
        self.status_code = status_code


class RetryExhausted(FetchError):
    """Tentativas esgotadas (erro de rede, timeout, 429 ou 5xx)."""

    def __init__(self, url: str, attempts: int, last_error: str) -> None:
        super().__init__(f"Tentativas esgotadas ({attempts}) para {url}: {last_error}")
        self.url = url
        self.attempts = attempts
        self.last_error = last_error


class ParseError(FetchError):
    """Resposta recebida mas em formato inesperado."""


class ConnectorConfigError(VayaError):
    """Configuracao invalida para um conector."""


class ConnectorNotFound(VayaError):
    """Plataforma sem conector registrado."""


class SourceNotFound(VayaError):
    """Source inexistente."""


class BrowserUnavailable(VayaError):
    """Playwright/Chromium indisponivel (extra `browser` ou `playwright install chromium`)."""
