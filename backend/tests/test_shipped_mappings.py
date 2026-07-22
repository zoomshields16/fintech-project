# Regression guard on the REAL mappings.json — the file the app actually ships.
#
# The other test files use synthetic specs to pin the engine's semantics; this one
# pins the CONTENT. export_mappings.py rebuilds mappings.json wholesale from the
# workbook, and running it without apply_overrides.py silently reverts our four
# mapping decisions (~4.5 points of pass rate, no error raised). If that ever
# happens again, this file is what fails.

import json
from pathlib import Path

import pytest

MAPPINGS = Path(__file__).resolve().parents[1] / "mappings.json"


@pytest.fixture(scope="module")
def spec():
    return json.loads(MAPPINGS.read_text())["statements"]


def _priority(rows, model_line, synonym):
    return next((r["priority"] for r in rows
                 if r["model_line"] == model_line and r["synonym"] == synonym), None)


def test_net_income_check_targets_the_pre_nci_field(spec):
    """Our net income is pretax - tax (pre-NCI); FMP's netIncome is post-NCI.
    The check must compare like with like. Carson approved this over his
    per-ticker reclass approach — apply_overrides.py override 2."""
    rows = spec["income_statement"]["master"]
    assert _priority(rows, "Net Income (reported)", "netIncomeFromContinuingOperations") == 1
    assert _priority(rows, "Net Income (reported)", "netIncome") == 2


def test_total_equity_check_targets_the_including_nci_field(spec):
    """totalStockholdersEquity excludes non-controlling interest; our subtotal
    includes it. totalEquity is the like-for-like figure — override 1."""
    rows = spec["balance_sheet"]["master"]
    assert _priority(rows, "Total Equity (reported)", "totalEquity") == 1
    assert _priority(rows, "Total Equity (reported)", "totalStockholdersEquity") == 2


def test_preferred_stock_issuance_is_mapped(spec):
    """Unmapped, financing cash flow comes up short by the issuance — override 3."""
    rows = spec["cash_flow"]["master"]
    assert _priority(rows, "Other Financing Activities", "netPreferredStockIssuance") == 1


def test_redundant_other_income_reclasses_are_dropped(spec):
    """The workbook's per-ticker reclasses into Other Income patch the same NCI
    gap override 2 closes globally — keeping both double-counts, and they break
    pretax_income by the amount they fix net_income — override 4."""
    reclasses = spec["income_statement"]["reclasses"]
    assert [r for r in reclasses if r["to_line"] == "Other Income (Expense)"] == []


def test_every_master_row_has_the_fields_the_engine_reads(spec):
    """The engine indexes these four keys on every row without .get() fallbacks."""
    for statement, tables in spec.items():
        for r in tables["master"]:
            for key in ("model_line", "synonym", "use_type", "priority"):
                assert key in r, f"{statement} row missing {key}: {r}"
            assert isinstance(r["priority"], int)
