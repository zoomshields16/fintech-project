# Balance sheet engine.
#
# Mapping rules come from mappings.json (exported from Carson's workbook) and are
# resolved per ticker by mapping_engine. This file owns the fold from Carson's
# model lines down to the app's display keys, the subtotal formulas, and the
# reconcile against FMP's reported totals.
#
# The fold mirrors his Inputs sheet: his map splits some concepts into more lines
# than the app displays (Short-Term Debt vs Current Portion of LTD, Deferred
# Revenue, Income Taxes Payable, Operating Lease Liabilities, Tax Assets,
# Noncontrolling Interest), and each of those folds into the display line his
# Inputs sheet folds it into — so our subtotals are composed of the same pieces
# as his.

from checks import compare_line, to_display
from mapping_engine import pull_aliased, resolve_line_value

STATEMENT = "balance_sheet"

# snake_case key -> Carson model line(s) folded into it
BS_ALIASES = {
    # --- Current Assets ---
    "cash_st_investments":  ["Cash and Cash Equivalents", "Short-Term Investments"],
    "accounts_receivable":  ["Accounts Receivable"],
    "inventory":            ["Inventory"],
    # Prepaids have no line of their own in Carson's map — his Other Current Assets
    # pulls prepaids as a Priority-1 tie, so they arrive inside other_current.
    "other_current":        ["Other Current Assets"],
    # --- Non-Current Assets ---
    "ppe_net":              ["PP&E"],
    "goodwill":             ["Goodwill"],
    "intangible_assets":    ["Intangible Assets"],
    "lt_investments":       ["Long-Term Investments"],
    "other_noncurrent":     ["Other Long-Term Assets", "Tax Assets"],
    # --- Current Liabilities ---
    "accounts_payable":     ["Accounts Payable"],
    "accrued_expenses":     ["Accrued Expenses"],
    "current_ltd":          ["Current Portion of Long-Term Debt", "Short-Term Debt"],
    "other_current_liab":   ["Other Current Liabilities", "Deferred Revenue", "Income Taxes Payable"],
    # --- Non-Current Liabilities ---
    "long_term_debt":       ["Long-Term Debt"],
    "deferred_tax_liab":    ["Deferred Tax Liabilities"],
    "other_lt_liab":        ["Other Long-Term Liabilities", "Operating Lease Liabilities"],
    "unearned_revenue_lt":  ["Deferred Revenue (Non-Current)"],
    # --- Shareholders' Equity ---
    "common_stock":         ["Common Stock / APIC"],
    "apic":                 ["Additional Paid-In Capital"],
    "retained_earnings":    ["Retained Earnings"],
    "treasury_stock":       ["Treasury Stock"],
    "aoci":                 ["AOCI"],
    "other_equity":         ["Noncontrolling Interest"],
}

# Reconcile targets: our computed subtotal -> Carson's reported-check model line.
# He checks only the totals FMP actually reports; the non-current subtotals are
# covered transitively by Total Assets / Total Liabilities.
BS_CHECK_LINES = {
    "total_current_assets": "Total Current Assets (reported)",
    "total_assets":         "Total Assets (reported)",
    "total_current_liab":   "Total Current Liabilities (reported)",
    "total_liabilities":    "Total Liabilities (reported)",
    "total_equity":         "Total Equity (reported)",
    "total_lae":            "Total Liab & Equity (reported)",
}


def pull_bs_accounts(bs_record, records=None, ticker=None):
    """Pull one year's detail lines. `records` is the full history — synonym
    resolution spans all years."""
    records = records if records is not None else [bs_record]
    a = pull_aliased(STATEMENT, bs_record, records, BS_ALIASES, ticker)
    a["prepaid"] = 0.0  # kept for API shape; folded into other_current (see BS_ALIASES)
    return a


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


def reconcile_bs_rows(bs_record, computed, records=None):
    """Structured reconcile results — one row per line item. Persisted to check_results."""
    records = records if records is not None else [bs_record]
    return [
        compare_line(line, computed.get(line),
                     resolve_line_value(STATEMENT, check_line, bs_record, records))
        for line, check_line in BS_CHECK_LINES.items()
    ]


def reconcile_bs(bs_record, computed, records=None):
    """Compare our computed BS subtotals against FMP's reported values (display form)."""
    return to_display(reconcile_bs_rows(bs_record, computed, records))
