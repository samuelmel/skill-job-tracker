"""Testes contra a API real do Greenhouse (rede). Rodar com: pytest -m live."""

import pytest

from vaya_indexer.connectors.greenhouse import GreenhouseConnector
from vaya_indexer.core.exceptions import FetchError
from vaya_indexer.extraction.http import HttpClient

pytestmark = pytest.mark.live

CANDIDATE_BOARDS = ("gitlab", "airbnb", "discord", "stripe")


async def test_greenhouse_real_board_normalizes_jobs() -> None:
    errors: list[str] = []
    async with HttpClient(user_agent="VayaJobIndexer/0.1 (live-test)", timeout=30) as http:
        for board in CANDIDATE_BOARDS:
            connector = GreenhouseConnector(http, board_token=board)
            try:
                result = await connector.fetch_jobs()
            except FetchError as exc:
                errors.append(f"{board}: {exc}")
                continue
            if not result.jobs:
                errors.append(f"{board}: sem vagas")
                continue
            normalized = [connector.normalize(raw) for raw in result.jobs]
            assert len(normalized) >= 1
            assert all(job.title for job in normalized)
            assert all(job.original_url for job in normalized)
            return
    pytest.fail("Nenhum board respondeu com vagas: " + "; ".join(errors))
