# Carson's BS Synonym Map — 0/1/2 priority logic.
# TO UPDATE: only edit BS_DETAIL_ACCOUNTS and BS_CHECK_ACCOUNTS below.
# Never touch the functions — logic lives in statement_engine.py.
#
# Priority: 0=unconditional single source, 1=preferred, 2=fallback

from statement_engine import pull_accounts_priority

BS_DETAIL_ACCOUNTS = {
    # --- Current Assets ---
    "cash_st_investments":  [(1, "cashAndShortTermInvestments"), (2, "cashAndCashEquivalents")],
    "accounts_receivable":  [(1, "netReceivables"), (2, "accountsReceivables")],
    "inventory":            [(0, "inventory")],
    "prepaid":              [(0, "prepaids")],
    "other_current":        [(0, "otherCurrentAssets")],
    # --- Non-Current Assets ---
    "ppe_net":              [(0, "propertyPlantEquipmentNet")],
    "goodwill":             [(0, "goodwill")],
    "intangible_assets":    [(0, "intangibleAssets")],
    "lt_investments":       [(0, "longTermInvestments")],
    "other_noncurrent":     [(0, "otherNonCurrentAssets")],
    # --- Current Liabilities ---
    "accounts_payable":     [(1, "accountPayables"), (2, "totalPayables")],
    "accrued_expenses":     [(0, "accruedExpenses")],
    "current_ltd":          [(0, "shortTermDebt")],
    "other_current_liab":   [(0, "otherCurrentLiabilities")],
    # --- Non-Current Liabilities ---
    "long_term_debt":       [(0, "longTermDebt")],
    "deferred_tax_liab":    [(0, "deferredTaxLiabilitiesNonCurrent")],
    "other_lt_liab":        [(0, "otherNonCurrentLiabilities")],
    "unearned_revenue_lt":  [(0, "deferredRevenueNonCurrent")],
    # --- Shareholders' Equity ---
    "common_stock":         [(0, "commonStock")],
    "apic":                 [(0, "additionalPaidInCapital")],
    "retained_earnings":    [(0, "retainedEarnings")],
    "treasury_stock":       [(0, "treasuryStock")],
    "aoci":                 [(0, "accumulatedOtherComprehensiveIncomeLoss")],
    "other_equity":         [(0, "otherTotalStockholdersEquity")],
}

# Keys match computed keys in compute_bs_formula_lines for direct reconciliation lookup
BS_CHECK_ACCOUNTS = {
    "total_current_assets":    [(0, "totalCurrentAssets")],
    "total_noncurrent_assets": [(0, "totalNonCurrentAssets")],
    "total_assets":            [(0, "totalAssets")],
    "total_current_liab":      [(0, "totalCurrentLiabilities")],
    "total_noncurrent_liab":   [(0, "totalNonCurrentLiabilities")],
    "total_liabilities":       [(0, "totalLiabilities")],
    "total_equity":            [(1, "totalStockholdersEquity"), (2, "totalEquity")],
    "total_lae":               [(0, "totalLiabilitiesAndTotalEquity")],
}


def pull_bs_accounts(bs_record):
    return pull_accounts_priority(bs_record, BS_DETAIL_ACCOUNTS)


def compute_bs_formula_lines(a):
    """Compute BS subtotals from detail accounts. None values treated as 0."""
    def s(val):
        return val or 0

    total_current_assets = (s(a["cash_st_investments"]) + s(a["accounts_receivable"]) +
                            s(a["inventory"]) + s(a["prepaid"]) + s(a["other_current"]))
    total_noncurrent_assets = (s(a["ppe_net"]) + s(a["goodwill"]) + s(a["intangible_assets"]) +
                               s(a["lt_investments"]) + s(a["other_noncurrent"]))
    total_assets = total_current_assets + total_noncurrent_assets

    total_current_liab = (s(a["accounts_payable"]) + s(a["accrued_expenses"]) +
                          s(a["current_ltd"]) + s(a["other_current_liab"]))
    total_noncurrent_liab = (s(a["long_term_debt"]) + s(a["deferred_tax_liab"]) +
                             s(a["other_lt_liab"]) + s(a["unearned_revenue_lt"]))
    total_liabilities = total_current_liab + total_noncurrent_liab

    total_equity = (s(a["common_stock"]) + s(a["apic"]) + s(a["retained_earnings"]) +
                    s(a["treasury_stock"]) + s(a["aoci"]) + s(a["other_equity"]))
    total_lae = total_liabilities + total_equity
    # Accounting identity check: Total Assets = Total Liabilities + Total Equity
    check_balance = total_assets - total_lae

    return {
        "total_current_assets":    total_current_assets,
        "total_noncurrent_assets": total_noncurrent_assets,
        "total_assets":            total_assets,
        "total_current_liab":      total_current_liab,
        "total_noncurrent_liab":   total_noncurrent_liab,
        "total_liabilities":       total_liabilities,
        "total_equity":            total_equity,
        "total_lae":               total_lae,
        "check_balance":           check_balance,
    }


def reconcile_bs(bs_record, computed):
    """Compare our computed BS subtotals against FMP's reported values."""
    reported = pull_accounts_priority(bs_record, BS_CHECK_ACCOUNTS)
    results = {}
    for line, rep_val in reported.items():
        ours = computed.get(line)
        if rep_val is None or ours is None:
            results[line] = "no reported value"
        elif abs(ours - rep_val) < 1:
            results[line] = "MATCH"
        else:
            results[line] = f"MISMATCH (ours={ours:,.0f}, reported={rep_val:,.0f})"
    return results
