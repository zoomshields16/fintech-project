# The supported universe, and the fact that it is enforced.
#
# The scope rule (Nasdaq-100, US-listed, US GAAP subtotals) lives in three
# places that have to agree: the exclusion set, the fetch guard, and the
# endpoint the frontend validates against. Nothing fails loudly if they drift —
# an unsupported ticker just quietly gets a company row and burns API calls, the
# way FER did until Aug 5, 2026. These pin the agreement.

import pytest
from fastapi.testclient import TestClient

import main
from data_source import get_financials_cached, UnsupportedTicker
from universe import EXCLUDED_TICKERS, is_supported, universe_symbols


@pytest.fixture
def client():
    return TestClient(main.app)


def test_endpoint_serves_the_same_list_the_guard_enforces(client):
    """The frontend must never offer a ticker the backend will refuse."""
    served = client.get("/api/universe").json()["symbols"]
    assert served == universe_symbols()
    assert all(is_supported(symbol) for symbol in served)


def test_excluded_tickers_are_absent_everywhere(client):
    body = client.get("/api/universe").json()
    for ticker in EXCLUDED_TICKERS:
        assert ticker not in body["symbols"]
        assert not is_supported(ticker)
    assert body["excluded"] == sorted(EXCLUDED_TICKERS)


@pytest.mark.parametrize("ticker", ["FER", "RACE", "ELF", "F", "BRK.B", "TXR", ""])
def test_out_of_scope_tickers_never_reach_fmp(ticker):
    """The guard raises BEFORE the network call — that is the whole point of it.

    A ticker that is merely wrong (TXR) costs nothing; one that is real but
    out of scope (F) costs nothing either. Both used to cost a fetch.
    """
    with pytest.raises(UnsupportedTicker):
        get_financials_cached(ticker)


def test_foreign_domicile_is_not_disqualifying():
    """Scope is about the reporting standard, not the country.

    ASML (Netherlands), MELI (Argentina) and PDD (China) all reconcile at 100%.
    A future 'US companies only' rule that drops them would be removing clean
    data, so this asserts the current, deliberate intent.
    """
    for ticker in ("ASML", "MELI", "PDD"):
        assert is_supported(ticker)
