"""Contrato de TODOS os conectores registrados. Novo conector entra aqui automaticamente."""

import json
from typing import Any

import pytest

from skilometer.connectors.base import BaseConnector, NormalizedJob, RawJob
from skilometer.connectors.registry import get_connector_class, registered_names
from skilometer.coverage.metadata import load_platform_metadata

NAMES = registered_names()


def _instances(name: str) -> list[dict[str, Any]]:
    return [
        i.config
        for meta in load_platform_metadata().values()
        if meta.connector_type == name
        for i in meta.example_instances
    ]


def test_there_are_registered_connectors() -> None:
    assert "greenhouse" in NAMES


@pytest.mark.parametrize("name", NAMES)
def test_platform_name_is_unique_and_consistent(name: str) -> None:
    cls = get_connector_class(name)
    assert cls.platform_name == name
    classes = [get_connector_class(n) for n in NAMES]
    assert classes.count(cls) == 1
    assert isinstance(cls.requires_browser, bool)


@pytest.mark.parametrize("name", NAMES)
def test_connector_is_declared_in_platforms_json(name: str) -> None:
    owners = [s for s, m in load_platform_metadata().items() if m.connector_type == name]
    assert owners, f"nenhuma plataforma de platforms.json declara connector_type='{name}'"


@pytest.mark.parametrize("name", NAMES)
def test_config_model_validates_example_instances(name: str) -> None:
    cls = get_connector_class(name)
    instances = _instances(name)
    assert instances, f"'{name}' precisa de example_instances em platforms.json (verify)"
    for config in instances:
        validated = cls.validate_config(config)
        assert cls.external_id_from_config(validated)


@pytest.mark.parametrize("name", NAMES)
def test_fixture_normalizes_to_valid_jobs(name: str) -> None:
    cls: type[BaseConnector] = get_connector_class(name)
    if cls.fixture_path is None:
        pytest.skip(f"'{name}' nao fornece fixture_path")
    data = json.loads(cls.fixture_path.read_text(encoding="utf-8"))
    assert data.get("synthetic") is True, "fixture deve ser marcada como sintetica"
    raw_jobs = [RawJob.model_validate(r) for r in data["raw_jobs"]]
    assert raw_jobs
    assert len({r.external_id for r in raw_jobs}) == len(raw_jobs)
    connector = cls.__new__(cls)  # normalize nao usa rede: sem HttpClient
    connector.config = cls.validate_config(_instances(name)[0])
    for raw in raw_jobs:
        job = connector.normalize(raw)
        assert isinstance(job, NormalizedJob)
        NormalizedJob.model_validate(job.model_dump())
        assert job.title.strip()
