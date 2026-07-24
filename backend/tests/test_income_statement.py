# The income-statement build. Pins the one piece of engine logic that lives
# outside mapping_engine's alias fold: Other Operating Expenses is resolved
# separately (it has no Detail anchor in Carson's map), so reclass adjustments
# have to be applied to it explicitly. Before that wiring, a workbook reclass
# targeting the line was parsed, stored, and silently ignored — the exact class
# of gap the from_line fix closed for balance-sheet removals.

import income_statement as inc
from conftest import patch_mappings, row

IS = "income_statement"

MASTER = [
    row("Revenue", "revenue"),
    row("Cost of Goods Sold", "costOfRevenue"),
    row("SG&A", "sellingGeneralAndAdministrativeExpenses"),
    row("Research & Development", "researchAndDevelopmentExpenses"),
    row("Other Operating Expenses", "otherExpenses", use_type="Check"),
]

RECORD = {
    "calendarYear": 2017,
    "revenue": 100.0,
    "costOfRevenue": 40.0,
    "sellingGeneralAndAdministrativeExpenses": 10.0,
    "researchAndDevelopmentExpenses": 5.0,
    "otherExpenses": 2.0,
}


def _operating_income(ticker):
    a = inc.pull_detail_accounts(RECORD, [RECORD], ticker=ticker)
    return inc.compute_formula_lines(a)["operating_income"]


def test_reclass_into_other_opex_lowers_operating_income(monkeypatch):
    """The TXN case: a 10-K charge FMP folds into operatingIncome without
    surfacing in any itemized field. Only a reclass can supply it, so the
    reclass must actually reach the built statement."""
    patch_mappings(monkeypatch, {IS: {"master": MASTER, "reclasses": [
        {"ticker": "TXN", "fiscal_year": 2017,
         "from_line": "FMP unitemized acquisition charges",
         "to_line": "Other Operating Expenses", "amount": 3.0},
    ]}})
    # gross 60 - (sga 10 + rnd 5 + other 2) = 43 without the reclass
    assert _operating_income("TXN") == 40.0


def test_reclass_only_hits_its_own_ticker_and_year(monkeypatch):
    patch_mappings(monkeypatch, {IS: {"master": MASTER, "reclasses": [
        {"ticker": "TXN", "fiscal_year": 2018,  # different year
         "from_line": "", "to_line": "Other Operating Expenses", "amount": 3.0},
    ]}})
    assert _operating_income("TXN") == 43.0
    assert _operating_income("AAPL") == 43.0
