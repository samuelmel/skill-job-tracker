"""Registro de conectores por nome de plataforma."""

from collections.abc import Callable
from typing import Any

from vaya_indexer.connectors.base import BaseConnector
from vaya_indexer.core.exceptions import BrowserUnavailable, ConnectorNotFound
from vaya_indexer.extraction.browser import BrowserFetcher
from vaya_indexer.extraction.http import HttpClient

_REGISTRY: dict[str, type[BaseConnector]] = {}


def register[C: type[BaseConnector]](name: str) -> Callable[[C], C]:
    def decorator(cls: C) -> C:
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"Conector '{name}' ja registrado")
        cls.platform_name = name
        _REGISTRY[name] = cls
        return cls

    return decorator


def _ensure_loaded() -> None:
    # Importar o modulo dispara o decorator @register.
    from vaya_indexer.connectors import (  # noqa: F401
        ashby,
        bamboohr,
        breezy_hr,
        gem_com,
        greenhouse,
        hirehive,
        lever_co,
        manatal,
        pinpoint,
        polymer,
        recruitee,
        rippling,
        workable,
        workday,
    )


def get_connector_class(name: str) -> type[BaseConnector]:
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ConnectorNotFound(f"Nenhum conector registrado para '{name}'") from None


def registered_names() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def create_connector(
    name: str,
    config: dict[str, Any],
    http: HttpClient,
    browser: BrowserFetcher | None = None,
) -> BaseConnector:
    cls = get_connector_class(name)
    if cls.requires_browser and browser is None:
        raise BrowserUnavailable(f"Conector '{name}' exige BrowserFetcher (requires_browser)")
    connector = cls(http, **config)
    if cls.requires_browser:
        connector.browser = browser
    return connector
