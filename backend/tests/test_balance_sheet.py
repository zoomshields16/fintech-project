# The equity roll-forward and the balance-sheet subtotal formulas, plus
# Carson's Income-Taxes-Payable guard (the one conditional fold in the pull).

import balance_sheet as bs
from conftest import patch_mappings, row


# ---------------------------------------------------- compute_equity_rollforward

def _roll(**overrides):
    """A year whose movement is fully explained; tests override one piece."""
    args = dict(prev_equity=1000.0, prev_aoci=-50.0,
                curr_equity=1090.0, curr_aoci=-30.0,   # OCI = +20
                net_income=200.0, dividends_paid=-50.0, stock_repurchased=-100.0,
                stock_comp=30.0, other_financing=-10.0)
    args.update(overrides)
    return bs.compute_equity_rollforward(**args)


def test_rollforward_fully_explained_year_has_zero_residual():
    """1000 + 200 NI + 20 OCI - 50 div - 100 buyback + 30 SBC - 10 other = 1090."""
    out = _roll()
    assert out["explained_change"] == 90.0
    assert out["actual_change"] == 90.0
    assert out["residual"] == 0.0


def test_rollforward_reports_unexplained_movement_as_residual():
    """An extra 25 of equity the terms don't cover must surface, not be absorbed."""
    out = _roll(curr_equity=1115.0)
    assert out["residual"] == 25.0
    assert out["explained_change"] == 90.0  # the explained part is unchanged


def test_rollforward_oci_is_the_change_in_aoci():
    out = _roll(prev_aoci=-30.0, curr_aoci=-50.0, curr_equity=1050.0)
    assert out["oci"] == -20.0
    assert out["residual"] == 0.0


def test_rollforward_negative_cash_flow_lines_are_added_not_subtracted():
    """Dividends/buybacks arrive negative from FMP's cash flow. If the code ever
    subtracts them, this year comes out over-explained by exactly 2x those lines."""
    out = _roll()
    assert out["dividends"] == -50.0
    assert out["buybacks"] == -100.0


def test_rollforward_treats_none_as_zero():
    out = bs.compute_equity_rollforward(None, None, None, None,
                                        None, None, None, None, None)
    assert out["residual"] == 0.0
    assert out["ending_equity"] == 0.0


# ------------------------------------------------------- compute_bs_formula_lines

def _accounts(**overrides):
    keys = ["cash_st_investments", "accounts_receivable", "inventory", "prepaid",
            "other_current", "ppe_net", "goodwill", "intangible_assets",
            "lt_investments", "other_noncurrent", "accounts_payable",
            "accrued_expenses", "current_ltd", "other_current_liab",
            "long_term_debt", "deferred_tax_liab", "other_lt_liab",
            "unearned_revenue_lt", "common_stock", "apic", "retained_earnings",
            "treasury_stock", "aoci", "other_equity"]
    a = {k: 0.0 for k in keys}
    a.update(overrides)
    return a


def test_bs_subtotals_and_balance_identity():
    out = bs.compute_bs_formula_lines(_accounts(
        cash_st_investments=100.0, inventory=50.0,      # current assets 150
        ppe_net=350.0,                                  # total assets 500
        accounts_payable=80.0,                          # current liab 80
        long_term_debt=220.0,                           # total liab 300
        retained_earnings=250.0, treasury_stock=-50.0,  # equity 200
    ))
    assert out["total_current_assets"] == 150.0
    assert out["total_assets"] == 500.0
    assert out["total_liabilities"] == 300.0
    assert out["total_equity"] == 200.0
    assert out["total_lae"] == 500.0
    assert out["check_balance"] == 0.0  # A = L + E holds


def test_bs_imbalance_surfaces_in_check_balance():
    out = bs.compute_bs_formula_lines(_accounts(cash_st_investments=500.0,
                                                retained_earnings=490.0))
    assert out["check_balance"] == 10.0


# ------------------------------------------------------ Income Taxes Payable guard

def _bs_spec():
    return {"balance_sheet": {"master": [
        row("Accounts Payable", "accountPayables", priority=1),
        row("Income Taxes Payable", "taxPayables", priority=1),
        row("Total Current Liabilities (reported)", "totalCurrentLiabilities",
            use_type="Total/Check", priority=1),
    ], "reclasses": []}}


def test_itp_counted_when_it_fits_the_reported_gap(monkeypatch):
    """AP 100M + ITP 20M = reported 120M: ITP genuinely separate, so count it."""
    patch_mappings(monkeypatch, _bs_spec())
    rec = {"accountPayables": 100e6, "taxPayables": 20e6,
           "totalCurrentLiabilities": 120e6}
    a = bs.pull_bs_accounts(rec, [rec])
    assert a["other_current_liab"] == 20e6


def test_itp_skipped_when_fmp_already_embedded_it_elsewhere(monkeypatch):
    """Reported total is only 100M, so the 20M of taxPayables is already inside
    another field — counting it again would double it."""
    patch_mappings(monkeypatch, _bs_spec())
    rec = {"accountPayables": 100e6, "taxPayables": 20e6,
           "totalCurrentLiabilities": 100e6}
    a = bs.pull_bs_accounts(rec, [rec])
    assert a["other_current_liab"] == 0.0


def test_itp_counted_when_no_reported_total_to_check_against(monkeypatch):
    patch_mappings(monkeypatch, _bs_spec())
    rec = {"accountPayables": 100e6, "taxPayables": 20e6}
    a = bs.pull_bs_accounts(rec, [rec])
    assert a["other_current_liab"] == 20e6
