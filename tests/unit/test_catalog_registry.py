import pytest

from vaya_indexer.connectors.greenhouse import GreenhouseConnector
from vaya_indexer.connectors.registry import (
    create_connector,
    get_connector_class,
    registered_names,
)
from vaya_indexer.core.exceptions import ConnectorConfigError, ConnectorNotFound
from vaya_indexer.database.models import ImplementationStatus, PlatformCategory
from vaya_indexer.extraction.http import HttpClient
from vaya_indexer.platforms_catalog import build_catalog


def test_catalog_has_78_unique_platforms() -> None:
    catalog = build_catalog()
    assert len(catalog) == 78
    assert sum(e.category is PlatformCategory.ATS for e in catalog) == 58
    assert sum(e.category is PlatformCategory.JOB_BOARD for e in catalog) == 20
    assert len({e.name for e in catalog}) == 78
    assert len({e.slug for e in catalog}) == 78


def test_catalog_statuses() -> None:
    catalog = {e.slug: e for e in build_catalog()}
    assert catalog["greenhouse"].implementation_status is ImplementationStatus.IMPLEMENTED
    others = [e for e in catalog.values() if e.slug != "greenhouse"]
    assert all(e.implementation_status is ImplementationStatus.NOT_STARTED for e in others)


def test_implemented_platforms_have_registered_connector() -> None:
    for entry in build_catalog():
        if entry.implementation_status is ImplementationStatus.IMPLEMENTED:
            assert entry.connector_type in registered_names()


async def test_registry_lookup_and_create() -> None:
    assert get_connector_class("greenhouse") is GreenhouseConnector
    async with HttpClient(user_agent="test") as http:
        connector = create_connector("greenhouse", {"board_token": "acme"}, http)
    assert isinstance(connector, GreenhouseConnector)
    assert connector.config.board_token == "acme"


def test_registry_unknown_connector() -> None:
    with pytest.raises(ConnectorNotFound):
        get_connector_class("nao-existe")


@pytest.mark.parametrize("config", [{}, {"board_token": ""}, {"board_token": "a b/c"}])
def test_greenhouse_config_validation_rejects(config: dict[str, str]) -> None:
    with pytest.raises(ConnectorConfigError):
        GreenhouseConnector.validate_config(config)
