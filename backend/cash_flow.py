# Cash flow engine.
#
# Mapping rules come from mappings.json (exported from Carson's workbook) and are
# resolved per ticker by mapping_engine. This file owns the fold to the app's
# snake_case keys, the subtotal formulas, and the reconcile against FMP's reported
# section totals.
#
# CF reclasses are different from IS/BS ones: the workbook moves value between
# SECTIONS (Operating/Investing/Financing), not between lines. They arrive here as
# _reclass_* keys and are added into the section subtotals.
#testing 2.0
from checks import compare_line, to_display
from mapping_engine import pull_aliased, resolve_line_value, reclass_adjustments, fiscal_year

STATEMENT = "cash_flow"

# snake_case key -> Carson model line(s)
CF_ALIASES = {
    # --- Operating Activities ---
    "net_income":            ["Net Income"],
    "depreciation":          ["Depreciation and Amortization"],
    "stock_comp":            ["Stock Based Compensation"],
    "deferred_tax":          ["Deferred Income Tax"],
    "change_ar":             ["Accounts Receivable"],
    "change_inventory":      ["Inventory"],
    "change_ap":             ["Accounts Payable"],
    "other_wc":              ["Other Working Capital"],
    "other_noncash":         ["Other Non-Cash Items"],
    # --- Investing Activities ---
    "capex":                 ["Capital Expenditures"],
    "acquisitions":          ["Acquisitions"],
    "purchases_investments": ["Purchases of Investments"],
    "sales_investments":     ["Sales/Maturities of Investments"],
    "other_investing":       ["Other Investing Activities"],
    # --- Financing Activities ---
    "long_term_debt":        ["Long-Term Net Debt Issuance"],
    "short_term_debt":       ["Short-Term Net Debt Issuance"],
    "stock_repurchased":     ["Common Stock Repurchased"],
    "stock_issued":          ["Common Stock Issuance"],
    "dividends_paid":        ["Net Dividends Paid"],
    "other_financing":       ["Other Financing Activities"],
}

# Single-figure lines pulled outside the alias fold: the summary rows are
# check-type in Carson's map, and Free Cash Flow is his calculated line.
CF_SINGLE_LINES = {
    "fx_effect":      "Effect of FX on Cash",
    "cash_beginning": "Cash at Beginning of Period",
    "cash_end":       "Cash at End of Period",
}

# Reconcile targets: our computed subtotal -> Carson's reported model line
#
# Free Cash Flow is deliberately NOT reconciled. FMP derives its freeCashFlow from
# `capitalExpenditure`, while our CapEx line maps to
# `investmentsInPropertyPlantAndEquipment` (priority 1, with capitalExpenditure kept
# only as a backup), so the two disagree whenever those FMP fields disagree — and
# ours is the better number. MSTR is the clearest case: FMP's capitalExpenditure
# absorbs about $22B of bitcoin purchases where ours is $13.5M of actual property
# and equipment. Grading one against the other measured the field choice, not our
# math, and it accounted for 52 of the 99 material recent mismatches.
#
# Nothing downstream needs the check either: the valuation runs on unlevered free
# cash flow from dcf_engine.compute_ufcf, which is built from NOPAT, D&A, CapEx and
# the change in working capital rather than from this line. Carson's call, July 23.
CF_CHECK_LINES = {
    "operating_cf":    "Net Cash Provided by Operating Activities",
    "investing_cf":    "Net Cash Provided by Investing Activities",
    "financing_cf":    "Net Cash Provided by Financing Activities",
    "net_change_cash": "Net Change in Cash",
}


def pull_cf_accounts(cf_record, records=None, ticker=None):
    """Pull one year's detail lines. `records` is the full history — synonym
    resolution spans all years."""
    records = records if records is not None else [cf_record]
    a = pull_aliased(STATEMENT, cf_record, records, CF_ALIASES, ticker)

    # Dividends: prefer the all-in net figure; fall back to common-only when the
    # net line has no data for this company.
    if not a["dividends_paid"]:
        fallback = resolve_line_value(STATEMENT, "Common Dividends Paid", cf_record, records)
        a["dividends_paid"] = fallback if fallback is not None else 0.0

    for key, line in CF_SINGLE_LINES.items():
        val = resolve_line_value(STATEMENT, line, cf_record, records)
        a[key] = val if val is not None else 0.0

    # Section-level reclasses for this ticker/year (keys are section names).
    year = fiscal_year(cf_record)
    adj = reclass_adjustments(STATEMENT, ticker) if ticker else {}
    a["_reclass_operating"] = adj.get((year, "Operating"), 0.0)
    a["_reclass_investing"] = adj.get((year, "Investing"), 0.0)
    a["_reclass_financing"] = adj.get((year, "Financing"), 0.0)
    a["_reclass_fx"] = adj.get((year, "FX"), 0.0)
    return a


def compute_cf_formula_lines(a):
    """Compute CF subtotals from detail accounts. None values treated as 0."""
    def s(val):
        return val or 0

    change_in_wc = s(a["change_ar"]) + s(a["change_inventory"]) + s(a["change_ap"]) + s(a["other_wc"])
    # Carson combines deferred_tax + other_noncash into a single "other adjustments" line
    other_adjustments = s(a["deferred_tax"]) + s(a["other_noncash"])
    operating_cf = (s(a["net_income"]) + s(a["depreciation"]) + s(a["stock_comp"]) +
                    other_adjustments + change_in_wc +
                    s(a.get("_reclass_operating")))
    # Model total: investing activities are modeled as capex only (per Carson's template).
    # The full sum is kept separately so reconcile can still validate the mapping vs FMP.
    investing_cf = s(a["capex"])
    investing_cf_full = (s(a["capex"]) + s(a["acquisitions"]) + s(a["purchases_investments"]) +
                         s(a["sales_investments"]) + s(a["other_investing"]) +
                         s(a.get("_reclass_investing")))
    # Model total: financing activities are modeled as buybacks + dividends only
    # (per Carson's template). Full sum kept for the FMP reconcile.
    financing_cf = s(a["stock_repurchased"]) + s(a["dividends_paid"])
    financing_cf_full = (s(a["long_term_debt"]) + s(a["short_term_debt"]) +
                         s(a["stock_repurchased"]) + s(a["stock_issued"]) +
                         s(a["dividends_paid"]) + s(a["other_financing"]) +
                         s(a.get("_reclass_financing")))
    # capex is already negative in FMP, so adding it reduces operating CF to get FCF
    free_cash_flow = operating_cf + s(a["capex"])
    # Modeled change in cash (no FX line, matching Carson's template);
    # actual cash balances still come from FMP so the balance sheet balances.
    net_change_cash = operating_cf + investing_cf + financing_cf
    net_change_cash_full = (operating_cf + investing_cf_full + financing_cf_full +
                            s(a["fx_effect"]) + s(a.get("_reclass_fx")))

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


def reconcile_cf_rows(cf_record, computed, records=None):
    """Structured reconcile results — one row per line item. Persisted to check_results."""
    records = records if records is not None else [cf_record]
    # investing_cf/financing_cf/net_change_cash are model totals (capex-only
    # investing); reconcile against the full sums so the check still validates
    # the mapping.
    reconcile_key = {
        "investing_cf": "investing_cf_full",
        "financing_cf": "financing_cf_full",
        "net_change_cash": "net_change_cash_full",
    }
    return [
        compare_line(line, computed.get(reconcile_key.get(line, line)),
                     resolve_line_value(STATEMENT, check_line, cf_record, records))
        for line, check_line in CF_CHECK_LINES.items()
    ]


def reconcile_cf(cf_record, computed, records=None):
    """Compare our computed CF subtotals against FMP's reported values (display form)."""
    return to_display(reconcile_cf_rows(cf_record, computed, records))
