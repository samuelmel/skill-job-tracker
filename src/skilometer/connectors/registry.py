"""Registro de conectores por nome de plataforma."""

from collections.abc import Callable
from typing import Any

from skilometer.connectors.base import BaseConnector
from skilometer.core.exceptions import BrowserUnavailable, ConnectorNotFound
from skilometer.extraction.browser import BrowserFetcher
from skilometer.extraction.http import HttpClient

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
    from skilometer.connectors import (  # noqa: F401
        applicantpro,
        ashby,
        avature,
        bamboohr,
        breezy_hr,
        careerplug,
        comeet,
        eightfold_ai,
        gem_com,
        greenhouse,
        hirehive,
        hireology,
        hiringthing,
        icims,
        isolvedhire,
        jazzhr,
        jboard_io,
        jobboard_io,
        jobboardhq,
        jobboardly,
        jobiqo,
        jobvite,
        join_com,
        lever_co,
        manatal,
        niceboard,
        oracle_recruiting,
        pageup,
        paylocity,
        personio,
        phenom_people,
        pinpoint,
        polymer,
        recooty,
        recruitee,
        rippling,
        smartjobboard,
        teamtailor,
        trakstar,
        trinet,
        workable,
        workday,
        wp_job_manager,
        zoho_recruit,
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
