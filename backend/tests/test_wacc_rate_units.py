# FMP hands back rates as percentages — 4.46 meaning 4.46% — while every formula
# downstream wants fractions. The treasury rate was normalized and the market risk
# premium was not, so beta * mrp came out about 100x too large.
#
# What made it survive is that nothing crashed: the WACC just became 547%, which
# discounted the terminal value to roughly nothing and produced a NEGATIVE equity
# value per share. A number that is merely absurd still renders. So these tests pin
# the units, not the arithmetic, and they cover both fields together — the two were
# only ever inconsistent because they were normalized in separate places.

import pytest
from fastapi.testclient import TestClient

import main


def _rates(client, treasury, mrp, monkeypatch):
    """Run the DCF against one pair of raw FMP rate values and report the WACC inputs."""
    raw = {
        "profile":             [{"beta": 1.0, "price": 100.0, "mktCap": 1_000_000_000}],
        "treasury_rates":      [{"year10": treasury}],
        "market_risk_premium": [{"country": "United States", "totalEquityRiskPremium": mrp}],
        "income_statement":    [], "balance_sheet": [], "cash_flow": [],
        # numberOfShares is what per-share value divides by; without it the endpoint
        # returns None there and the assertion below has nothing to test.
        "enterprise_values":   [{"numberOfShares": 100_000_000}],
        "levered_dcf":         [],
    }
    monkeypatch.setattr(main, "get_financials_cached", lambda t: (raw, None))
    monkeypatch.setattr(main, "live_price", lambda t, fallback=None: fallback or {})

    res = client.post("/api/run-dcf", json={
        "ticker": "TEST", "ufcf": [1e9] * 5, "terminal_growth_rate": 0.025,
    })
    assert res.status_code == 200
    body = res.json()
    assert "error" not in body, body
    return body


@pytest.fixture
def client():
    return TestClient(main.app)


def test_percentage_rates_are_normalized_to_fractions(client, monkeypatch):
    wi = _rates(client, treasury=4.68, mrp=4.46, monkeypatch=monkeypatch)["wacc_inputs"]
    assert wi["risk_free_rate"]      == pytest.approx(0.0468)
    assert wi["market_risk_premium"] == pytest.approx(0.0446)


def test_fractional_rates_are_left_alone(client, monkeypatch):
    # Should FMP ever switch to fractions, normalizing again would be just as wrong.
    wi = _rates(client, treasury=0.0468, mrp=0.0446, monkeypatch=monkeypatch)["wacc_inputs"]
    assert wi["risk_free_rate"]      == pytest.approx(0.0468)
    assert wi["market_risk_premium"] == pytest.approx(0.0446)


def test_wacc_lands_in_a_believable_range(client, monkeypatch):
    wi = _rates(client, treasury=4.68, mrp=4.46, monkeypatch=monkeypatch)["wacc_inputs"]
    assert 0.02 < wi["wacc"] < 0.30, f'wacc came out at {wi["wacc"]:.2%}'


def test_equity_value_per_share_is_positive_for_a_profitable_forecast(client, monkeypatch):
    # The symptom that surfaced the bug: five years of positive cash flow valued at
    # less than nothing, because the terminal value had been discounted into the floor.
    body = _rates(client, treasury=4.68, mrp=4.46, monkeypatch=monkeypatch)
    assert body["equity_value_per_share"] > 0
    assert body["pv_terminal"] > body["pv_discrete"], (
        "terminal value should dominate a 5-year DCF, not round to nothing")
