# Best / base / worst cases on /api/run-projection.
#
# Carson runs one switch (Drivers!C7) that INDEXes each Group 1 block — best, base,
# worst — while the Group 2 drivers sit outside it and are shared. The thing that can
# quietly go wrong here is the merge: if a case's Group 1 drivers fail to override the
# shared set, all three cases come back identical and the page still renders three
# perfectly plausible tabs. So the assertions below are that the cases actually DIFFER,
# in the direction the driver implies, and that Group 2 stays common to all of them.
#
# The data fetch and the mapping are stubbed out, so this needs no key and no database
# and grades the endpoint's orchestration against a company whose arithmetic is known.

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

import main
from test_projection_balances import LAST_IS, LAST_CF, LAST_BS

L = lambda v: [v] * 5

GROUP2 = dict(rnd_pct=L(0.05), da_pct=L(0.04), tax_rate=L(0.20),
              sbc_pct=L(0.02), buyback_pct=L(0.10))

# Same shape as the frontend sends: growth is the headline difference between the
# cases, and the cost lines move the other way in the worst case.
CASES = {
    "best":  dict(revenue_growth=L(0.15), cogs_pct=L(0.55), sga_pct=L(0.09), capex_pct=L(0.04)),
    "base":  dict(revenue_growth=L(0.10), cogs_pct=L(0.60), sga_pct=L(0.10), capex_pct=L(0.05)),
    "worst": dict(revenue_growth=L(0.05), cogs_pct=L(0.65), sga_pct=L(0.11), capex_pct=L(0.06)),
}


@pytest.fixture
def client(monkeypatch):
    """The endpoint with its data layer replaced by the synthetic company above."""
    raw = {"income_statement": [{"date": "2025-12-31"}],
           "cash_flow":        [{"date": "2025-12-31"}],
           "balance_sheet":    [{"date": "2025-12-31"}]}

    # The subtotal helpers read every line the real mapping produces, so the stubs
    # default the ones this company does not carry to zero. Whether the mapping fills
    # them correctly is test_mapping_engine's job, not this file's.
    stub = lambda d: defaultdict(float, d)

    monkeypatch.setattr(main, "get_financials_cached", lambda t: (raw, None))
    monkeypatch.setattr(main, "pull_detail_accounts", lambda *a: stub(LAST_IS))
    monkeypatch.setattr(main, "pull_cf_accounts",     lambda *a: stub(LAST_CF))
    monkeypatch.setattr(main, "pull_bs_accounts",     lambda *a: stub(LAST_BS))
    return TestClient(main.app)


def post(client, **extra):
    body = {"ticker": "TEST", "drivers": {**CASES["base"], **GROUP2}, **extra}
    res = client.post("/api/run-projection", json=body)
    assert res.status_code == 200
    return res.json()


def test_each_case_comes_back(client):
    data = post(client, cases=CASES)
    assert set(data["cases"]) == {"best", "base", "worst"}


def test_the_cases_are_not_the_same_forecast(client):
    # The failure this catches: a merge that drops the per-case Group 1 drivers and
    # runs the shared set three times.
    rev = {name: case["income_statement"][0]["revenue"]
           for name, case in post(client, cases=CASES)["cases"].items()}
    assert rev["best"] > rev["base"] > rev["worst"]


def test_higher_growth_and_lower_costs_earn_more(client):
    cases = post(client, cases=CASES)["cases"]
    for year in range(5):
        ni = {name: c["income_statement"][year]["net_income"] for name, c in cases.items()}
        assert ni["best"] > ni["base"] > ni["worst"], f"year {year + 1}: {ni}"


def test_group_2_drivers_are_shared_across_cases(client):
    # D&A is a Group 2 driver at 4% of revenue in every case, so it must track each
    # case's own revenue and nothing else. If a case ever carried its own Group 2 set
    # this ratio would drift.
    for case in post(client, cases=CASES)["cases"].values():
        for is_yr, cf_yr in zip(case["income_statement"], case["cash_flow"]):
            assert cf_yr["depreciation"] == pytest.approx(is_yr["revenue"] * 0.04)


def test_every_case_still_balances(client):
    for name, case in post(client, cases=CASES)["cases"].items():
        for year in case["balance_sheet"]:
            assert abs(year["check_balance"]) < 0.01, (
                f'{name} {year["date"]} is off by {year["check_balance"]:,.2f}')


def test_top_level_keys_mirror_the_base_case(client):
    # Kept so anything reading the response the way it looked before cases existed —
    # the DCF hand-off included — still finds a complete projection at the top level.
    data = post(client, cases=CASES)
    assert data["income_statement"] == data["cases"]["base"]["income_statement"]
    assert data["ufcf"] == data["cases"]["base"]["ufcf"]


def test_a_request_without_cases_is_unchanged(client):
    data = post(client)
    assert "cases" not in data
    assert len(data["income_statement"]) == 5
    assert len(data["ufcf"]) == 5


def test_unknown_case_names_are_rejected(client):
    data = post(client, cases={"bull": CASES["best"]})
    assert "Unknown case" in data["error"]
