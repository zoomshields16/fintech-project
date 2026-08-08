# Projection engine — Group 1 (Revenue, COGS, SG&A, CapEx) + Group 2 (R&D, D&A, Tax, SBC, Buybacks).
# Group 2 drivers are optional single floats; if omitted, the engine falls back to last actual year values.
# Sign conventions match FMP / our historical engines:
#   revenue positive, cogs positive (subtracted in IS), sga positive, capex negative (cash outflow).


def _s(val):
    return val or 0


# Carson drives forecast working capital off days ratios, not off last year's cash
# movement: Model!G173 is (AR days / days in period) * revenue, G174 and G175 the same
# against COGS. The days themselves are a three-year average of history
# (G168 = AVERAGE(D168:F168)), which is why a single odd year cannot set the whole
# forecast. See [Carson owns the finance logic].
DAYS_IN_PERIOD = 365
WC_HISTORY_YEARS = 3


def _days_ratio(balance, flow):
    """Days of a flow held on the balance sheet: (balance / flow) * 365."""
    return (balance / flow) * DAYS_IN_PERIOD if flow else 0.0


def working_capital_days(is_history, bs_history, years=WC_HISTORY_YEARS):
    """Average AR / inventory / AP days over recent history — Model!G168:G170.

    Takes already-mapped historical records, newest first. Years with no revenue or
    no COGS contribute nothing rather than a divide-by-zero.
    """
    ar_days, inv_days, ap_days = [], [], []

    for is_yr, bs_yr in list(zip(is_history, bs_history))[:years]:
        revenue = _s(is_yr.get("revenue"))
        cogs    = _s(is_yr.get("cogs"))
        if revenue:
            ar_days.append(_days_ratio(_s(bs_yr.get("accounts_receivable")), revenue))
        if cogs:
            inv_days.append(_days_ratio(_s(bs_yr.get("inventory")), cogs))
            ap_days.append(_days_ratio(_s(bs_yr.get("accounts_payable")), cogs))

    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    return {"ar_days": mean(ar_days), "inventory_days": mean(inv_days), "ap_days": mean(ap_days)}


def project_working_capital(projected_is, wc_days):
    """Forecast AR / inventory / AP balances — Model!G173:G175.

    These are computed BEFORE the cash flow, because Carson derives the cash flow's
    working-capital movements from the change in these balances rather than the other
    way around. Getting that direction backwards is what stopped the forecast years
    balancing: the cash flow moved cash for working capital every year while the
    balance sheet held the same accounts flat.
    """
    return [
        {
            "accounts_receivable": (wc_days["ar_days"] / DAYS_IN_PERIOD) * _s(yr.get("revenue")),
            "inventory":           (wc_days["inventory_days"] / DAYS_IN_PERIOD) * _s(yr.get("cogs")),
            "accounts_payable":    (wc_days["ap_days"] / DAYS_IN_PERIOD) * _s(yr.get("cogs")),
        }
        for yr in projected_is
    ]


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


def project_cash_flow(projected_is, last_cf_actuals, drivers,
                      projected_wc=None, last_bs_actuals=None):
    """
    Group 1: capex_pct (per-year list)
    Group 2 (optional single floats): da_pct, sbc_pct, buyback_pct
    Falls back to last actual year values when absent.

    `projected_wc` and `last_bs_actuals` carry the forecast working-capital balances
    from project_working_capital. Given them, the working-capital lines are derived
    from the change in those balances — Model!G62:G64 — instead of repeating last
    year's movement forever. They are optional only so the historical-only callers
    and older tests keep working; a forecast without them will not balance.
    """
    # Group 2 — use driver if provided, else fall back to last year
    da_pct       = drivers.get("da_pct")       # D&A as fraction of revenue
    sbc_pct      = drivers.get("sbc_pct")      # SBC as fraction of revenue
    buyback_pct  = drivers.get("buyback_pct")  # buybacks as fraction of net income (Model!G79)

    # Fallbacks held at last year
    dep_held      = _s(last_cf_actuals.get("depreciation"))
    sbc_held      = _s(last_cf_actuals.get("stock_comp"))
    repurch_held  = _s(last_cf_actuals.get("stock_repurchased"))  # negative

    # Everything else held constant
    other_adj     = _s(last_cf_actuals.get("other_adjustments"))
    held_change_ar     = _s(last_cf_actuals.get("change_ar"))
    held_change_inv    = _s(last_cf_actuals.get("change_inventory"))
    held_change_ap     = _s(last_cf_actuals.get("change_ap"))
    other_wc      = _s(last_cf_actuals.get("other_wc"))
    dividends     = _s(last_cf_actuals.get("dividends_paid"))

    # Prior-year working-capital balances, to difference the first forecast year against.
    prev_wc = {
        "accounts_receivable": _s((last_bs_actuals or {}).get("accounts_receivable")),
        "inventory":           _s((last_bs_actuals or {}).get("inventory")),
        "accounts_payable":    _s((last_bs_actuals or {}).get("accounts_payable")),
    }

    years = []
    for i, is_yr in enumerate(projected_is):
        revenue    = is_yr["revenue"]
        net_income = is_yr["net_income"]
        capex        = -(revenue * drivers["capex_pct"][i])
        depreciation = (revenue * da_pct[i])       if da_pct      is not None else dep_held
        stock_comp   = (revenue * sbc_pct[i])      if sbc_pct     is not None else sbc_held
        # Buybacks scale with net income, not revenue: Model!G79 = (G50 * Drivers!F80) * -1.
        repurchased  = -(net_income * buyback_pct[i]) if buyback_pct is not None else repurch_held

        if projected_wc is not None and last_bs_actuals is not None:
            wc = projected_wc[i]
            # Cash falls when a receivable or inventory balance grows, and rises when a
            # payable grows — hence the sign flip on the asset side (Model!G62:G63).
            change_ar  = -(wc["accounts_receivable"] - prev_wc["accounts_receivable"])
            change_inv = -(wc["inventory"] - prev_wc["inventory"])
            change_ap  = (wc["accounts_payable"] - prev_wc["accounts_payable"])
            prev_wc = wc
            # Carson zeroes these in the forecast (Model!G59 deferred taxes, G68 other),
            # and derives the accrued / prepaid / other-current-liability movements from
            # balances he holds flat, so those come out at zero too. Carrying last year's
            # values forward instead moves cash every year with nothing on the balance
            # sheet to match it, which is a constant imbalance that never closes.
            period_other_adj, period_other_wc = 0.0, 0.0
        else:
            change_ar, change_inv, change_ap = held_change_ar, held_change_inv, held_change_ap
            period_other_adj, period_other_wc = other_adj, other_wc

        operating_cf    = (net_income + depreciation + stock_comp + period_other_adj
                           + change_ar + change_inv + change_ap + period_other_wc)
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
            "other_adjustments":     period_other_adj,
            "change_ar":             change_ar,
            "change_inventory":      change_inv,
            "change_ap":             change_ap,
            "other_wc":              period_other_wc,
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


def project_balance_sheet(projected_is, projected_cf, last_bs_actuals, projected_wc=None):
    """
    Roll the balance sheet forward year by year, following Model!G93:G129.

    Every cash-flow line that moves cash has a balance-sheet counterpart here, which
    is what makes the years balance:

        cash              prior + net change in cash          Model!G93
        AR / inventory    the working-capital schedule        Model!G94, G95
        AP                the working-capital schedule        Model!G109
        PPE               prior + capex + depreciation        Model!G205
        retained earnings prior + net income + dividends      Model!G125
        treasury stock    prior + buybacks                    Model!G126
        other equity      prior + stock-based compensation    Model!G128

    Note SBC accumulates in OTHER EQUITY, not APIC (Carson holds APIC flat at G124).
    Without that line the forecast is short by the SBC added back in operating cash
    flow every year, which was one of the gaps that stopped this balancing.

    `projected_wc` is optional so historical-only callers still work, but a forecast
    without it holds working capital flat and will not balance.
    """
    # Starting balances
    prev_cash     = _s(last_bs_actuals.get("cash_st_investments"))
    prev_retained = _s(last_bs_actuals.get("retained_earnings"))
    prev_ppe      = _s(last_bs_actuals.get("ppe_net"))
    prev_treasury = _s(last_bs_actuals.get("treasury_stock"))
    prev_other_eq = _s(last_bs_actuals.get("other_equity"))
    prev_ar        = _s(last_bs_actuals.get("accounts_receivable"))
    prev_inventory = _s(last_bs_actuals.get("inventory"))
    prev_ap        = _s(last_bs_actuals.get("accounts_payable"))

    # Held constant
    prepaid         = _s(last_bs_actuals.get("prepaid"))
    other_current   = _s(last_bs_actuals.get("other_current"))
    goodwill        = _s(last_bs_actuals.get("goodwill"))
    intangibles     = _s(last_bs_actuals.get("intangible_assets"))
    lt_investments  = _s(last_bs_actuals.get("lt_investments"))
    other_nc        = _s(last_bs_actuals.get("other_noncurrent"))
    accrued         = _s(last_bs_actuals.get("accrued_expenses"))
    current_ltd     = _s(last_bs_actuals.get("current_ltd"))
    other_cl        = _s(last_bs_actuals.get("other_current_liab"))
    lt_debt         = _s(last_bs_actuals.get("long_term_debt"))
    deferred_tax    = _s(last_bs_actuals.get("deferred_tax_liab"))
    other_lt        = _s(last_bs_actuals.get("other_lt_liab"))
    unearned_lt     = _s(last_bs_actuals.get("unearned_revenue_lt"))
    common_stock    = _s(last_bs_actuals.get("common_stock"))
    apic            = _s(last_bs_actuals.get("apic"))   # flat — SBC lands in other equity
    aoci            = _s(last_bs_actuals.get("aoci"))

    years = []
    for i, (is_yr, cf_yr) in enumerate(zip(projected_is, projected_cf)):
        # Roll forward
        cash = prev_cash + cf_yr["net_change_cash"]
        # Dividends are already negative, so this subtracts them (Model!G125).
        retained_earnings = prev_retained + is_yr["net_income"] + cf_yr["dividends_paid"]
        # PPE net = prior PPE + capex (capex is negative) - depreciation removed from books
        # capex is negative (cash out) but adds to asset; flip sign to add
        ppe_net = prev_ppe + (-cf_yr["capex"]) - cf_yr["depreciation"]
        # Buybacks are negative and make treasury stock more negative (Model!G126).
        treasury = prev_treasury + cf_yr["stock_repurchased"]
        # The counterpart to the SBC added back in operating cash flow (Model!G128).
        other_eq = prev_other_eq + cf_yr["stock_comp"]

        if projected_wc is not None:
            ar        = projected_wc[i]["accounts_receivable"]
            inventory = projected_wc[i]["inventory"]
            ap        = projected_wc[i]["accounts_payable"]
        else:
            ar, inventory, ap = prev_ar, prev_inventory, prev_ap

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

        prev_cash      = cash
        prev_retained  = retained_earnings
        prev_ppe       = ppe_net
        prev_treasury  = treasury
        prev_other_eq  = other_eq
        prev_ar        = ar
        prev_inventory = inventory
        prev_ap        = ap

    return years
