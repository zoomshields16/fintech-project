# The valuation math, graded against arithmetic done by hand. Sign conventions
# are the thing most worth pinning: CapEx and the shareholder-return lines
# arrive negative from the cash-flow statement, and the code adds them.

from pytest import approx

from dcf_engine import compute_ufcf, compute_wacc, run_dcf


# ----------------------------------------------------------------- compute_ufcf

def test_ufcf_hand_computed_single_year():
    """NOPAT 80 + D&A 10 + CapEx (-20) + change in NWC (-5) = 65.

    NWC goes 20 -> 25 (a build of 5 consumes cash), expressed prior - current.
    """
    projected_is = [{"operating_income": 100.0, "pretax_income": 90.0, "income_tax": 18.0}]
    projected_cf = [{"depreciation": 10.0, "capex": -20.0}]
    projected_bs = [{"accounts_receivable": 30.0, "inventory": 10.0, "accounts_payable": 15.0}]
    last_actuals = {"accounts_receivable": 25.0, "inventory": 10.0, "accounts_payable": 15.0}

    ufcf = compute_ufcf(projected_is, projected_cf, projected_bs, last_actuals)
    assert ufcf == [65.0]


def test_ufcf_tax_rate_is_zero_when_pretax_not_positive():
    """A loss year implies no meaningful effective rate — NOPAT falls back to EBIT."""
    projected_is = [{"operating_income": 100.0, "pretax_income": -10.0, "income_tax": 2.0}]
    projected_cf = [{"depreciation": 0.0, "capex": 0.0}]
    bs = [{"accounts_receivable": 0.0, "inventory": 0.0, "accounts_payable": 0.0}]

    ufcf = compute_ufcf(projected_is, projected_cf, bs, bs[0])
    assert ufcf == [100.0]


def test_ufcf_nwc_chains_through_projected_years():
    """Year 1 deltas off the last actuals; year 2 deltas off year 1's projection."""
    is_years = [{"operating_income": 0.0, "pretax_income": 0.0, "income_tax": 0.0}] * 2
    cf_years = [{"depreciation": 0.0, "capex": 0.0}] * 2
    bs_years = [
        {"accounts_receivable": 30.0, "inventory": 0.0, "accounts_payable": 0.0},  # NWC 30
        {"accounts_receivable": 20.0, "inventory": 0.0, "accounts_payable": 0.0},  # NWC 20
    ]
    last_actuals = {"accounts_receivable": 25.0, "inventory": 0.0, "accounts_payable": 0.0}

    ufcf = compute_ufcf(is_years, cf_years, bs_years, last_actuals)
    assert ufcf == [-5.0, 10.0]  # 25->30 consumes 5; 30->20 releases 10


def test_ufcf_missing_fields_are_treated_as_zero():
    assert compute_ufcf([{}], [{}], [{}], {}) == [0.0]


# ----------------------------------------------------------------- compute_wacc

def test_wacc_hand_computed():
    """CoE 4% + 1.2*5% = 10%; after-tax CoD 5%*(1-25%) = 3.75%; weights 80/20."""
    out = compute_wacc(beta=1.2, risk_free_rate=0.04, market_risk_premium=0.05,
                       total_debt=200.0, market_cap=800.0,
                       pre_tax_cost_of_debt=0.05, tax_rate=0.25)
    assert out["cost_of_equity"] == approx(0.10)
    assert out["after_tax_cost_debt"] == approx(0.0375)
    assert out["wacc"] == approx(0.10 * 0.8 + 0.0375 * 0.2)


def test_wacc_zero_total_capital_is_an_error_not_a_crash():
    out = compute_wacc(1.0, 0.04, 0.05, total_debt=0.0, market_cap=0.0,
                       pre_tax_cost_of_debt=0.05, tax_rate=0.25)
    assert "error" in out


# ---------------------------------------------------------------------- run_dcf

def test_dcf_hand_computed_gordon_growth():
    """One 100 cash flow, WACC 10%, TGR 2%:

    TV = 102 / 0.08 = 1275; EV = (100 + 1275) / 1.1 = 1250 exactly.
    Equity = 1250 - 250 net debt = 1000; / 100 shares = 10.
    """
    out = run_dcf([100.0], wacc=0.10, terminal_growth_rate=0.02,
                  shares_outstanding=100.0, net_debt=250.0)
    assert out["terminal_value"] == 1275.0
    assert abs(out["enterprise_value"] - 1250.0) < 1e-9
    assert abs(out["equity_value_per_share"] - 10.0) < 1e-9


def test_dcf_net_cash_adds_to_equity_value():
    """Negative net debt (net cash) makes equity worth MORE than the enterprise."""
    out = run_dcf([100.0], 0.10, 0.02, shares_outstanding=100.0, net_debt=-50.0)
    assert out["equity_value"] > out["enterprise_value"]


def test_dcf_rejects_wacc_at_or_below_terminal_growth():
    """Gordon Growth divides by (WACC - g) — at or below g the formula is meaningless."""
    assert "error" in run_dcf([100.0], 0.02, 0.02, 100.0, 0.0)
    assert "error" in run_dcf([100.0], 0.015, 0.02, 100.0, 0.0)


def test_dcf_per_share_is_none_without_share_count():
    out = run_dcf([100.0], 0.10, 0.02, shares_outstanding=0, net_debt=0.0)
    assert out["equity_value_per_share"] is None
