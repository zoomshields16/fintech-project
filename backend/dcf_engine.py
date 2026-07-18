def _operating_nwc(bs_year):
    """Operating working capital the model projects: receivables + inventory - payables."""
    return ((bs_year.get("accounts_receivable") or 0)
            + (bs_year.get("inventory") or 0)
            - (bs_year.get("accounts_payable") or 0))


def compute_ufcf(projected_is, projected_cf, projected_bs, last_bs_actuals):
    """Unlevered free cash flow per projected year, bottom-up:

        UFCF = NOPAT + D&A - CapEx - change in NWC

    Starts from EBIT (operating income), so financing and other comprehensive
    income are excluded by construction — an unlevered measure must not carry
    either. CapEx arrives negative from the cash-flow projection, so it is added
    rather than subtracted; the same is true of the working-capital delta, which
    is expressed as prior minus current (a build in NWC consumes cash).

    The tax rate is the effective rate implied by each projected year
    (income tax / pretax income), falling back to zero when pretax income is not
    positive.
    """
    prior_nwc = _operating_nwc(last_bs_actuals)
    proj_nwc = [_operating_nwc(bs_year) for bs_year in projected_bs]

    ufcf = []
    for i, is_year in enumerate(projected_is):
        cf_year = projected_cf[i]

        ebit = is_year.get("operating_income") or 0
        pretax = is_year.get("pretax_income") or 0
        tax = is_year.get("income_tax") or 0
        tax_rate = tax / pretax if pretax > 0 else 0
        nopat = ebit * (1 - tax_rate)

        depreciation = cf_year.get("depreciation") or 0
        capex = cf_year.get("capex") or 0          # already negative
        previous = prior_nwc if i == 0 else proj_nwc[i - 1]
        change_nwc = previous - proj_nwc[i]

        ufcf.append(nopat + depreciation + capex + change_nwc)

    return ufcf


def compute_wacc(beta, risk_free_rate, market_risk_premium,
                 total_debt, market_cap, pre_tax_cost_of_debt, tax_rate):
    equity_risk_premium  = beta * market_risk_premium
    cost_of_equity       = risk_free_rate + equity_risk_premium
    after_tax_cost_debt  = pre_tax_cost_of_debt * (1 - tax_rate)
    total_cap            = total_debt + market_cap
    if total_cap == 0:
        return {"error": "zero total capital"}
    weight_equity = market_cap  / total_cap
    weight_debt   = total_debt  / total_cap
    wacc = cost_of_equity * weight_equity + after_tax_cost_debt * weight_debt
    return {
        "equity_risk_premium":   equity_risk_premium,
        "cost_of_equity":        cost_of_equity,
        "after_tax_cost_debt":   after_tax_cost_debt,
        "weight_equity":         weight_equity,
        "weight_debt":           weight_debt,
        "wacc":                  wacc,
    }


def run_dcf(ufcf_list, wacc, terminal_growth_rate, shares_outstanding, net_debt):
    """
    Gordon Growth terminal value. Terminal value is discounted from year n.
    PV of discrete = sum of years 1..n discounted individually.
    Equity Value = Enterprise Value - Net Debt  (negative net_debt = net cash => adds to EV).
    """
    if wacc <= terminal_growth_rate:
        return {"error": "WACC must exceed terminal growth rate"}

    n = len(ufcf_list)

    # Terminal value on last UFCF
    terminal_cf    = ufcf_list[-1] * (1 + terminal_growth_rate)
    terminal_value = terminal_cf / (wacc - terminal_growth_rate)

    # Discount each discrete UFCF year
    pv_discrete = sum(ufcf_list[i] / (1 + wacc) ** (i + 1) for i in range(n))
    pv_terminal = terminal_value / (1 + wacc) ** n

    enterprise_value       = pv_discrete + pv_terminal
    equity_value           = enterprise_value - net_debt
    equity_value_per_share = equity_value / shares_outstanding if shares_outstanding else None

    return {
        "terminal_cf":            terminal_cf,
        "terminal_value":         terminal_value,
        "pv_discrete":            pv_discrete,
        "pv_terminal":            pv_terminal,
        "enterprise_value":       enterprise_value,
        "equity_value":           equity_value,
        "equity_value_per_share": equity_value_per_share,
    }


def sensitivity_tables(ufcf_list, base_wacc, shares_outstanding, net_debt, stock_price=None):
    """
    4 × 5×5 sensitivity tables matching Carson's DCF sheet layout.
    WACC steps: base ± 1%, ± 0.5%  (5 rows)
    TGR steps:  1.5%, 2%, 2.5%, 3%, 3.5%  (5 cols)
    Tables: Enterprise Value, Equity Per Share, Equity Value, Premium/Discount
    """
    wacc_steps = [
        base_wacc - 0.01,
        base_wacc - 0.005,
        base_wacc,
        base_wacc + 0.005,
        base_wacc + 0.01,
    ]
    tgr_steps = [0.015, 0.02, 0.025, 0.03, 0.035]

    ev_table       = []
    per_share_table = []
    eq_value_table  = []
    premium_table   = []

    for w in wacc_steps:
        ev_row = []
        ps_row = []
        eq_row = []
        pr_row = []
        for g in tgr_steps:
            if w <= g:
                ev_row.append(None)
                ps_row.append(None)
                eq_row.append(None)
                pr_row.append(None)
            else:
                result = run_dcf(ufcf_list, w, g, shares_outstanding, net_debt)
                ev  = result.get("enterprise_value")
                ps  = result.get("equity_value_per_share")
                eq  = result.get("equity_value")
                pr  = ((ps / stock_price) - 1) if (ps and stock_price) else None
                ev_row.append(ev)
                ps_row.append(ps)
                eq_row.append(eq)
                pr_row.append(pr)
        ev_table.append(ev_row)
        per_share_table.append(ps_row)
        eq_value_table.append(eq_row)
        premium_table.append(pr_row)

    return {
        "wacc_steps":               wacc_steps,
        "tgr_steps":                tgr_steps,
        "enterprise_value_table":   ev_table,
        "equity_per_share_table":   per_share_table,
        "equity_value_table":       eq_value_table,
        "premium_discount_table":   premium_table,
    }
