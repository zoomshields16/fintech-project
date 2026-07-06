# Projection engine — Group 1 (Revenue, COGS, SG&A, CapEx) + Group 2 (R&D, D&A, Tax, SBC, Buybacks).
# Group 2 drivers are optional single floats; if omitted, the engine falls back to last actual year values.
# Sign conventions match FMP / our historical engines:
#   revenue positive, cogs positive (subtracted in IS), sga positive, capex negative (cash outflow).


def _s(val):
    return val or 0


def project_income_statement(last_actuals, drivers, base_year):
    """
    Group 1 drivers (per-year lists): revenue_growth, cogs_pct, sga_pct
    Group 2 drivers (single floats, optional): rnd_pct, tax_rate
    Falls back to last actual year values when Group 2 drivers are absent.
    """
    # Group 1
    revenue_growth = drivers["revenue_growth"]
    cogs_pct       = drivers["cogs_pct"]
    sga_pct        = drivers["sga_pct"]

    # Group 2 — use driver if provided, else fall back to last year
    rnd_pct        = drivers.get("rnd_pct")       # fraction of revenue or None
    tax_rate_input = drivers.get("tax_rate")       # fraction of pretax or None

    # Fallbacks held at last year
    rnd_held        = _s(last_actuals.get("rnd"))
    other_opex      = _s(last_actuals.get("other_opex"))
    interest_income = _s(last_actuals.get("interest_income"))
    interest_expense= _s(last_actuals.get("interest_expense"))
    other_income    = _s(last_actuals.get("other_income"))
    last_pretax     = _s(last_actuals.get("pretax_income"))
    last_tax        = _s(last_actuals.get("income_tax"))
    fallback_tax    = (last_tax / last_pretax) if last_pretax else 0

    years = []
    prev_revenue = last_actuals["revenue"]

    for i in range(5):
        revenue      = prev_revenue * (1 + revenue_growth[i])
        cogs         = revenue * cogs_pct[i]
        gross_profit = revenue - cogs
        sga          = revenue * sga_pct[i]
        rnd          = (revenue * rnd_pct[i]) if rnd_pct is not None else rnd_held
        total_opex   = sga + rnd + other_opex
        operating_income = gross_profit - total_opex
        pretax_income    = operating_income + interest_income - interest_expense + other_income
        effective_tax    = tax_rate_input[i] if tax_rate_input is not None else fallback_tax
        income_tax       = pretax_income * effective_tax
        net_income       = pretax_income - income_tax

        years.append({
            "date":             f"{base_year + i + 1}",
            "projected":        True,
            "revenue":          revenue,
            "cogs":             cogs,
            "gross_profit":     gross_profit,
            "sga":              sga,
            "rnd":              rnd,
            "other_opex":       other_opex,
            "total_opex":       total_opex,
            "operating_income": operating_income,
            "interest_income":  interest_income,
            "interest_expense": interest_expense,
            "other_income":     other_income,
            "pretax_income":    pretax_income,
            "income_tax":       income_tax,
            "net_income":       net_income,
            "eps":              None,
            "eps_diluted":      None,
            "reconcile":        {},
        })
        prev_revenue = revenue

    return years


def project_cash_flow(projected_is, last_cf_actuals, drivers):
    """
    Group 1: capex_pct (per-year list)
    Group 2 (optional single floats): da_pct, sbc_pct, buyback_pct
    Falls back to last actual year values when absent.
    """
    # Group 2 — use driver if provided, else fall back to last year
    da_pct       = drivers.get("da_pct")       # D&A as fraction of revenue
    sbc_pct      = drivers.get("sbc_pct")      # SBC as fraction of revenue
    buyback_pct  = drivers.get("buyback_pct")  # buybacks as fraction of revenue (produces negative)

    # Fallbacks held at last year
    dep_held      = _s(last_cf_actuals.get("depreciation"))
    sbc_held      = _s(last_cf_actuals.get("stock_comp"))
    repurch_held  = _s(last_cf_actuals.get("stock_repurchased"))  # negative

    # Everything else held constant
    other_adj     = _s(last_cf_actuals.get("other_adjustments"))
    change_ar     = _s(last_cf_actuals.get("change_ar"))
    change_inv    = _s(last_cf_actuals.get("change_inventory"))
    change_ap     = _s(last_cf_actuals.get("change_ap"))
    other_wc      = _s(last_cf_actuals.get("other_wc"))
    dividends     = _s(last_cf_actuals.get("dividends_paid"))

    years = []
    for i, is_yr in enumerate(projected_is):
        revenue    = is_yr["revenue"]
        net_income = is_yr["net_income"]
        capex        = -(revenue * drivers["capex_pct"][i])
        depreciation = (revenue * da_pct[i])       if da_pct      is not None else dep_held
        stock_comp   = (revenue * sbc_pct[i])      if sbc_pct     is not None else sbc_held
        repurchased  = -(revenue * buyback_pct[i]) if buyback_pct is not None else repurch_held

        operating_cf    = net_income + depreciation + stock_comp + other_adj + change_ar + change_inv + change_ap + other_wc
        # Investing activities are modeled as capex only (per Carson's template)
        investing_cf    = capex
        # Financing activities are modeled as buybacks + dividends only (per Carson's template)
        financing_cf    = repurchased + dividends
        free_cash_flow  = operating_cf + capex
        net_change_cash = operating_cf + investing_cf + financing_cf

        years.append({
            "date":                  is_yr["date"],
            "projected":             True,
            "net_income":            net_income,
            "depreciation":          depreciation,
            "stock_comp":            stock_comp,
            "other_adjustments":     other_adj,
            "change_ar":             change_ar,
            "change_inventory":      change_inv,
            "change_ap":             change_ap,
            "other_wc":              other_wc,
            "operating_cf":          operating_cf,
            "capex":                 capex,
            "investing_cf":          investing_cf,
            "stock_repurchased":     repurchased,
            "dividends_paid":        dividends,
            "financing_cf":          financing_cf,
            "net_change_cash":       net_change_cash,
            "cash_beginning":        None,
            "cash_end":              None,
            "free_cash_flow":        free_cash_flow,
            "reconcile":             {},
        })

    return years


def project_balance_sheet(projected_is, projected_cf, last_bs_actuals):
    """
    Roll the balance sheet forward year by year.
    Cash accumulates via net_change_cash. Retained earnings add net income.
    PPE rolls via CapEx + D&A. Everything else held at last actual.
    """
    # Starting balances
    prev_cash     = _s(last_bs_actuals.get("cash_st_investments"))
    prev_retained = _s(last_bs_actuals.get("retained_earnings"))
    prev_ppe      = _s(last_bs_actuals.get("ppe_net"))

    # Held constant
    ar              = _s(last_bs_actuals.get("accounts_receivable"))
    inventory       = _s(last_bs_actuals.get("inventory"))
    prepaid         = _s(last_bs_actuals.get("prepaid"))
    other_current   = _s(last_bs_actuals.get("other_current"))
    goodwill        = _s(last_bs_actuals.get("goodwill"))
    intangibles     = _s(last_bs_actuals.get("intangible_assets"))
    lt_investments  = _s(last_bs_actuals.get("lt_investments"))
    other_nc        = _s(last_bs_actuals.get("other_noncurrent"))
    ap              = _s(last_bs_actuals.get("accounts_payable"))
    accrued         = _s(last_bs_actuals.get("accrued_expenses"))
    current_ltd     = _s(last_bs_actuals.get("current_ltd"))
    other_cl        = _s(last_bs_actuals.get("other_current_liab"))
    lt_debt         = _s(last_bs_actuals.get("long_term_debt"))
    deferred_tax    = _s(last_bs_actuals.get("deferred_tax_liab"))
    other_lt        = _s(last_bs_actuals.get("other_lt_liab"))
    unearned_lt     = _s(last_bs_actuals.get("unearned_revenue_lt"))
    common_stock    = _s(last_bs_actuals.get("common_stock"))
    apic            = _s(last_bs_actuals.get("apic"))
    treasury        = _s(last_bs_actuals.get("treasury_stock"))
    aoci            = _s(last_bs_actuals.get("aoci"))
    other_eq        = _s(last_bs_actuals.get("other_equity"))

    years = []
    for is_yr, cf_yr in zip(projected_is, projected_cf):
        # Roll forward
        cash = prev_cash + cf_yr["net_change_cash"]
        retained_earnings = prev_retained + is_yr["net_income"]
        # PPE net = prior PPE + capex (capex is negative) - depreciation removed from books
        # capex is negative (cash out) but adds to asset; flip sign to add
        ppe_net = prev_ppe + (-cf_yr["capex"]) - cf_yr["depreciation"]

        # Subtotals
        total_ca  = cash + ar + inventory + prepaid + other_current
        total_nca = ppe_net + goodwill + intangibles + lt_investments + other_nc
        total_assets = total_ca + total_nca

        total_cl  = ap + accrued + current_ltd + other_cl
        total_ncl = lt_debt + deferred_tax + other_lt + unearned_lt
        total_liab = total_cl + total_ncl

        total_eq  = common_stock + apic + retained_earnings + treasury + aoci + other_eq
        total_lae = total_liab + total_eq
        check     = total_assets - total_lae

        years.append({
            "date":                   is_yr["date"],
            "projected":              True,
            "cash_st_investments":    cash,
            "accounts_receivable":    ar,
            "inventory":              inventory,
            "prepaid":                prepaid,
            "other_current":          other_current,
            "total_current_assets":   total_ca,
            "ppe_net":                ppe_net,
            "goodwill":               goodwill,
            "intangible_assets":      intangibles,
            "lt_investments":         lt_investments,
            "other_noncurrent":       other_nc,
            "total_noncurrent_assets": total_nca,
            "total_assets":           total_assets,
            "accounts_payable":       ap,
            "accrued_expenses":       accrued,
            "current_ltd":            current_ltd,
            "other_current_liab":     other_cl,
            "total_current_liab":     total_cl,
            "long_term_debt":         lt_debt,
            "deferred_tax_liab":      deferred_tax,
            "other_lt_liab":          other_lt,
            "unearned_revenue_lt":    unearned_lt,
            "total_noncurrent_liab":  total_ncl,
            "total_liabilities":      total_liab,
            "common_stock":           common_stock,
            "apic":                   apic,
            "retained_earnings":      retained_earnings,
            "treasury_stock":         treasury,
            "aoci":                   aoci,
            "other_equity":           other_eq,
            "total_equity":           total_eq,
            "total_lae":              total_lae,
            "check_balance":          check,
            "reconcile":              {},
        })

        prev_cash     = cash
        prev_retained = retained_earnings
        prev_ppe      = ppe_net

    return years
