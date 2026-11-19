from vaya_indexer.connectors.greenhouse import GreenhouseConnector
from vaya_indexer.coverage.verification import (
    InstanceResult,
    decide_status,
    fixture_normalizes,
    is_transient,
)
from vaya_indexer.database.models import ImplementationStatus as S

OK = InstanceResult("a", success=True, jobs_found=3)
NET = InstanceResult("b", error="RetryExhausted: timeout", transient=True)
EMPTY = InstanceResult("c", error="fonte respondeu sem vagas")


def test_any_success_verifies() -> None:
    assert decide_status(S.IMPLEMENTED, [NET, OK], True) is S.VERIFIED
    assert decide_status(S.TESTED, [EMPTY, OK], False) is S.VERIFIED


def test_network_failure_keeps_previous_status() -> None:
    for current in (S.IMPLEMENTED, S.TESTED, S.VERIFIED):
        assert decide_status(current, [NET, NET], True) is current


def test_real_failure_downgrades_verified() -> None:
    assert decide_status(S.VERIFIED, [EMPTY], True) is S.TESTED
    assert decide_status(S.VERIFIED, [NET, EMPTY], False) is S.IMPLEMENTED
    assert decide_status(S.IMPLEMENTED, [EMPTY], True) is S.TESTED


def test_transient_classification() -> None:
    assert is_transient("RetryExhausted: x") and is_transient("BrowserUnavailable: y")
    assert not is_transient("HttpStatusError: HTTP 404") and not is_transient(None)


def test_fixture_normalizes_for_greenhouse() -> None:
    assert fixture_normalizes(GreenhouseConnector, {"board_token": "gitlab"})
