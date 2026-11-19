import json

import pytest

from vaya_indexer.connectors.registry import get_connector_class
from vaya_indexer.coverage.metadata import (
    PLATFORMS_JSON,
    SCHEMA_VERSION,
    load_platform_metadata,
    parse_platforms,
)
from vaya_indexer.database.models import ImplementationStatus
from vaya_indexer.platforms_catalog import ATS_NAMES, JOB_BOARD_NAMES, build_catalog, slugify


def test_json_covers_exactly_the_78_catalog_slugs() -> None:
    expected = {slugify(n) for n in (*ATS_NAMES, *JOB_BOARD_NAMES)}
    raw = json.loads(PLATFORMS_JSON.read_text(encoding="utf-8"))
    assert len(expected) == 78
    assert set(raw["platforms"]) == expected
    assert set(load_platform_metadata()) == expected


def test_example_instances_only_for_platforms_with_connector() -> None:
    for slug, meta in load_platform_metadata().items():
        if meta.example_instances:
            assert meta.connector_type, slug
            cls = get_connector_class(meta.connector_type)
            for instance in meta.example_instances:
                cls.validate_config(instance.config)


def test_greenhouse_real_instances() -> None:
    meta = load_platform_metadata()["greenhouse"]
    assert [i.config["board_token"] for i in meta.example_instances] == [
        "gitlab",
        "airbnb",
        "discord",
        "stripe",
    ]
    assert meta.connector_type == "greenhouse"


def test_catalog_carries_metadata() -> None:
    catalog = {e.slug: e for e in build_catalog()}
    gh = catalog["greenhouse"]
    assert gh.extraction_strategy and gh.docs_url and gh.notes
    assert gh.implementation_status is ImplementationStatus.IMPLEMENTED
    assert catalog["adp"].implementation_status is ImplementationStatus.NOT_STARTED


def test_parse_rejects_bad_schema_and_unknown_fields() -> None:
    with pytest.raises(ValueError):
        parse_platforms({"schema_version": 99, "platforms": {}})
    with pytest.raises(ValueError):
        parse_platforms({"schema_version": SCHEMA_VERSION, "platforms": {"x": {"surprise": 1}}})
    with pytest.raises(ValueError):  # instancias sem conector
        parse_platforms(
            {
                "schema_version": SCHEMA_VERSION,
                "platforms": {"x": {"example_instances": [{"company": "A", "config": {}}]}},
            }
        )
