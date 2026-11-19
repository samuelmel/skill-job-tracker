"""Registro de conectores por nome de plataforma."""

from collections.abc import Callable
from typing import Any

from vaya_indexer.connectors.base import BaseConnector
from vaya_indexer.core.exceptions import ConnectorNotFound
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
    from vaya_indexer.connectors import greenhouse  # noqa: F401


def get_connector_class(name: str) -> type[BaseConnector]:
    _ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ConnectorNotFound(f"Nenhum conector registrado para '{name}'") from None


def registered_names() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def create_connector(name: str, config: dict[str, Any], http: HttpClient) -> BaseConnector:
    return get_connector_class(name)(http, **config)
