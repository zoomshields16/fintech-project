# The forecast balance sheet has to balance, and it did not: every cash-flow line that
# moved cash had no counterpart on the balance sheet, so the gap grew by a fixed amount
# every projected year (AAPL was off by $154B by year five).
#
# These use a small synthetic company rather than real data so the arithmetic is
# checkable by hand and the tests need no database or network.

import projection_engine as pe

LAST_IS = {
    "date": "2025", "revenue": 1000.0, "cogs": 600.0, "sga": 100.0, "rnd": 50.0,
    "other_opex": 0.0, "interest_income": 0.0, "interest_expense": 0.0,
    "other_income": 0.0, "pretax_income": 250.0, "income_tax": 50.0, "net_income": 200.0,
}

LAST_CF = {
    "depreciation": 40.0, "stock_comp": 20.0, "stock_repurchased": -30.0,
    "other_adjustments": 7.0, "change_ar": -5.0, "change_inventory": -3.0,
    "change_ap": 4.0, "other_wc": 6.0, "dividends_paid": -25.0,
}

# Assets 1000 = liabilities 400 + equity 600, so the base year balances exactly and any
# gap the test sees was introduced by the projection itself.
LAST_BS = {
    "cash_st_investments": 300.0, "accounts_receivable": 120.0, "inventory": 80.0,
    "prepaid": 10.0, "other_current": 0.0, "ppe_net": 400.0, "goodwill": 50.0,
    "intangible_assets": 40.0, "lt_investments": 0.0, "other_noncurrent": 0.0,
    "accounts_payable": 90.0, "accrued_expenses": 60.0, "current_ltd": 50.0,
    "other_current_liab": 0.0, "long_term_debt": 200.0, "deferred_tax_liab": 0.0,
    "other_lt_liab": 0.0, "unearned_revenue_lt": 0.0,
    "common_stock": 10.0, "apic": 190.0, "retained_earnings": 500.0,
    "treasury_stock": -100.0, "aoci": 0.0, "other_equity": 0.0,
}

L = lambda v: [v] * 5
DRIVERS = dict(revenue_growth=L(0.10), cogs_pct=L(0.60), sga_pct=L(0.10),
               capex_pct=L(0.05), rnd_pct=L(0.05), da_pct=L(0.04),
               tax_rate=L(0.20), sbc_pct=L(0.02), buyback_pct=L(0.10))


def _project():
    proj_is = pe.project_income_statement(LAST_IS, DRIVERS, 2025)
    wc_days = pe.working_capital_days([LAST_IS], [LAST_BS])
    proj_wc = pe.project_working_capital(proj_is, wc_days)
    proj_cf = pe.project_cash_flow(proj_is, LAST_CF, DRIVERS,
                                   projected_wc=proj_wc, last_bs_actuals=LAST_BS)
    proj_bs = pe.project_balance_sheet(proj_is, proj_cf, LAST_BS, projected_wc=proj_wc)
    return proj_is, proj_cf, proj_bs, proj_wc


def test_every_forecast_year_balances():
    _, _, proj_bs, _ = _project()

    for year in proj_bs:
        assert abs(year["check_balance"]) < 0.01, (
            f'{year["date"]} is off by {year["check_balance"]:,.2f}')


def test_the_gap_does_not_grow_year_on_year():
    # The original bug's signature: a constant imbalance added every year. Checking the
    # STEP catches it even if some future base-year offset makes the level non-zero.
    _, _, proj_bs, _ = _project()

    gaps = [y["check_balance"] for y in proj_bs]
    steps = [gaps[0]] + [gaps[i] - gaps[i - 1] for i in range(1, len(gaps))]
    assert max(abs(s) for s in steps) < 0.01


def test_working_capital_moves_with_revenue():
    # Held flat, these were the largest single contributor to the imbalance.
    _, _, proj_bs, _ = _project()

    receivables = [y["accounts_receivable"] for y in proj_bs]
    assert receivables == sorted(receivables)
    assert receivables[0] > LAST_BS["accounts_receivable"]


def test_cash_flow_working_capital_is_derived_from_the_balance_sheet():
    # Carson's direction (Model!G63), and the reverse of what this engine used to do.
    _, proj_cf, proj_bs, _ = _project()

    prev_ar = LAST_BS["accounts_receivable"]
    for cf_year, bs_year in zip(proj_cf, proj_bs):
        assert cf_year["change_ar"] == -(bs_year["accounts_receivable"] - prev_ar)
        prev_ar = bs_year["accounts_receivable"]


def test_equity_absorbs_dividends_buybacks_and_stock_comp():
    _, proj_cf, proj_bs, _ = _project()

    first_cf, first_bs = proj_cf[0], proj_bs[0]
    # Dividends reduce retained earnings alongside net income (Model!G125).
    assert first_bs["retained_earnings"] == (
        LAST_BS["retained_earnings"] + first_cf["net_income"] + first_cf["dividends_paid"])
    # Buybacks push treasury stock further negative (Model!G126).
    assert first_bs["treasury_stock"] == (
        LAST_BS["treasury_stock"] + first_cf["stock_repurchased"])
    # SBC lands in other equity, NOT in APIC (Model!G128 vs G124).
    assert first_bs["other_equity"] == LAST_BS["other_equity"] + first_cf["stock_comp"]
    assert first_bs["apic"] == LAST_BS["apic"]


def test_buybacks_scale_with_net_income_not_revenue():
    # Model!G79 = (G50 * Drivers!F80) * -1, where G50 is net income.
    proj_is, proj_cf, _, _ = _project()

    for is_year, cf_year in zip(proj_is, proj_cf):
        assert cf_year["stock_repurchased"] == -(is_year["net_income"] * 0.10)


def test_carried_forward_noise_is_dropped_in_the_forecast():
    # other_adjustments and other_wc were held at last year's values forever, moving
    # cash annually with nothing on the balance sheet to match. Carson zeroes them.
    _, proj_cf, _, _ = _project()

    assert all(y["other_adjustments"] == 0 for y in proj_cf)
    assert all(y["other_wc"] == 0 for y in proj_cf)
