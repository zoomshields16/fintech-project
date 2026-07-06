# Carson's CF Synonym Map — 0/1/2 priority logic.
# TO UPDATE: only edit CF_DETAIL_ACCOUNTS and CF_CHECK_ACCOUNTS below.
# Never touch the functions — logic lives in statement_engine.py.
#
# Priority: 0=unconditional single source, 1=preferred, 2=fallback

from statement_engine import pull_accounts_priority

CF_DETAIL_ACCOUNTS = {
    # --- Operating Activities ---
    "net_income":           [(0, "netIncome")],
    "depreciation":         [(0, "depreciationAndAmortization")],
    "stock_comp":           [(0, "stockBasedCompensation")],
    "deferred_tax":         [(0, "deferredIncomeTax")],
    "change_ar":            [(0, "accountsReceivables")],
    "change_inventory":     [(0, "inventory")],
    "change_ap":            [(0, "accountsPayables")],
    "other_wc":             [(0, "otherWorkingCapital")],
    "other_noncash":        [(0, "otherNonCashItems")],
    # --- Investing Activities ---
    # investmentsInPropertyPlantAndEquipment is preferred — capitalExpenditure can bundle Other Investing (e.g. ZS)
    "capex":                [(1, "investmentsInPropertyPlantAndEquipment"), (2, "capitalExpenditure")],
    "acquisitions":         [(0, "acquisitionsNet")],
    "purchases_investments":[(0, "purchasesOfInvestments")],
    "sales_investments":    [(0, "salesMaturitiesOfInvestments")],
    "other_investing":      [(0, "otherInvestingActivities")],
    # --- Financing Activities ---
    # Debt split matches Carson's template: LT and ST shown as separate lines
    "long_term_debt":       [(0, "longTermNetDebtIssuance")],
    "short_term_debt":      [(0, "shortTermNetDebtIssuance")],
    "stock_repurchased":    [(0, "commonStockRepurchased")],
    "stock_issued":         [(0, "commonStockIssuance")],
    # dividends: prefer net total; fall back to common-only split
    "dividends_paid":       [(1, "netDividendsPaid"), (2, "commonDividendsPaid")],
    "other_financing":      [(0, "otherFinancingActivities")],
    # --- Summary ---
    "fx_effect":            [(0, "effectOfForexChangesOnCash")],
    "cash_beginning":       [(0, "cashAtBeginningOfPeriod")],
    "cash_end":             [(0, "cashAtEndOfPeriod")],
}

# Keys here match the computed keys in compute_cf_formula_lines so reconcile() is a direct lookup.
CF_CHECK_ACCOUNTS = {
    "operating_cf":    [(1, "netCashProvidedByOperatingActivities"), (2, "operatingCashFlow")],
    "investing_cf":    [(0, "netCashProvidedByInvestingActivities")],
    "financing_cf":    [(0, "netCashProvidedByFinancingActivities")],
    "net_change_cash": [(0, "netChangeInCash")],
    "free_cash_flow":  [(0, "freeCashFlow")],
}


def pull_cf_accounts(cf_record):
    return pull_accounts_priority(cf_record, CF_DETAIL_ACCOUNTS)


def compute_cf_formula_lines(a):
    """Compute CF subtotals from detail accounts. None values treated as 0."""
    def s(val):
        return val or 0

    change_in_wc = s(a["change_ar"]) + s(a["change_inventory"]) + s(a["change_ap"]) + s(a["other_wc"])
    # Carson combines deferred_tax + other_noncash into a single "other adjustments" line
    other_adjustments = s(a["deferred_tax"]) + s(a["other_noncash"])
    operating_cf = (s(a["net_income"]) + s(a["depreciation"]) + s(a["stock_comp"]) +
                    other_adjustments + change_in_wc)
    # Model total: investing activities are modeled as capex only (per Carson's template).
    # The full sum is kept separately so reconcile can still validate the mapping vs FMP.
    investing_cf = s(a["capex"])
    investing_cf_full = (s(a["capex"]) + s(a["acquisitions"]) + s(a["purchases_investments"]) +
                         s(a["sales_investments"]) + s(a["other_investing"]))
    # Model total: financing activities are modeled as buybacks + dividends only
    # (per Carson's template). Full sum kept for the FMP reconcile.
    financing_cf = s(a["stock_repurchased"]) + s(a["dividends_paid"])
    financing_cf_full = (s(a["long_term_debt"]) + s(a["short_term_debt"]) +
                         s(a["stock_repurchased"]) + s(a["stock_issued"]) +
                         s(a["dividends_paid"]) + s(a["other_financing"]))
    # capex is already negative in FMP, so adding it reduces operating CF to get FCF
    free_cash_flow = operating_cf + s(a["capex"])
    # Modeled change in cash (no FX line, matching Carson's template);
    # actual cash balances still come from FMP so the balance sheet balances.
    net_change_cash = operating_cf + investing_cf + financing_cf
    net_change_cash_full = operating_cf + investing_cf_full + financing_cf_full + s(a["fx_effect"])

    return {
        "other_adjustments": other_adjustments,
        "change_in_wc": change_in_wc,
        "operating_cf": operating_cf,
        "investing_cf": investing_cf,
        "investing_cf_full": investing_cf_full,
        "financing_cf": financing_cf,
        "financing_cf_full": financing_cf_full,
        "free_cash_flow": free_cash_flow,
        "net_change_cash": net_change_cash,
        "net_change_cash_full": net_change_cash_full,
    }


def reconcile_cf(cf_record, computed):
    """Compare our computed CF subtotals against FMP's reported values."""
    # investing_cf/net_change_cash are model totals (capex-only investing);
    # reconcile against the full sums so the check still validates the mapping.
    reconcile_key = {
        "investing_cf": "investing_cf_full",
        "financing_cf": "financing_cf_full",
        "net_change_cash": "net_change_cash_full",
    }
    reported = pull_accounts_priority(cf_record, CF_CHECK_ACCOUNTS)
    results = {}
    for line, rep_val in reported.items():
        ours = computed.get(reconcile_key.get(line, line))
        if rep_val is None or ours is None:
            results[line] = "no reported value"
        elif abs(ours - rep_val) < 1:
            results[line] = "MATCH"
        else:
            results[line] = f"MISMATCH (ours={ours:,.0f}, reported={rep_val:,.0f})"
    return results
