# Carson's BS Synonym Map — 0/1/2 priority logic (same system as cash flow).
# TO UPDATE: only edit BS_DETAIL_ACCOUNTS and BS_CHECK_ACCOUNTS below.
# Never touch the engine functions — logic lives in statement_engine.py.
#
# Priority format per line: [(priority, "fmp_field_name"), ...]
#   0 = unconditional single source (no conflict risk)
#   1 = preferred source; used if present, stops looking
#   2 = fallback; only used if no priority-1 field found

from statement_engine import find_value_priority, pull_accounts_priority

# TODO: fill in from Carson's BS synonym map (reference/live model 51.xlsm)
BS_DETAIL_ACCOUNTS = {
    # Examples showing the format — replace with real values from the xlsm:
    # "cash":                 [(0, "cashAndCashEquivalents")],
    # "receivables":          [(1, "netReceivables"), (2, "accountsReceivables")],
    # "inventory":            [(0, "inventory")],
    # "total_current_assets": [(0, "totalCurrentAssets")],
    # "ppe_net":              [(1, "propertyPlantEquipmentNet"), (2, "fixedAssets")],
    # "total_assets":         [(0, "totalAssets")],
    # "total_debt":           [(1, "longTermDebt"), (2, "totalDebt")],
    # "total_equity":         [(0, "totalStockholdersEquity")],
}

BS_CHECK_ACCOUNTS = {
    # Reported totals to reconcile our computed subtotals against:
    # "total_assets_reported":      [(0, "totalAssets")],
    # "total_liabilities_reported": [(0, "totalLiabilities")],
}


def pull_bs_accounts(bs_record):
    return pull_accounts_priority(bs_record, BS_DETAIL_ACCOUNTS)


# compute_formula_lines() and reconcile() to be added once BS_DETAIL_ACCOUNTS is complete
