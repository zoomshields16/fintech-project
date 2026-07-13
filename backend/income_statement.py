# Income statement engine.
#
# Mapping rules come from mappings.json (exported from Carson's workbook by
# export_mappings.py) and are resolved per ticker by mapping_engine. This file only
# owns: the translation from Carson's model-line names to the app's snake_case keys,
# the subtotal formulas, and the reconcile against FMP's reported figures.

from checks import compare_line, to_display
from mapping_engine import (
    resolve_active,
    resolve_line_value,
    pull_aliased,
    fiscal_year,
)

STATEMENT = "income_statement"

# snake_case key -> Carson model line(s) feeding it
IS_ALIASES = {
    "revenue":          ["Revenue"],
    "cogs":             ["Cost of Goods Sold"],
    "sga":              ["SG&A"],
    "rnd":              ["Research & Development"],
    "interest_income":  ["Interest Income"],
    "interest_expense": ["Interest Expense"],
    "other_income":     ["Other Income (Expense)"],
    "income_tax":       ["Income Tax Expense"],
    "eps":              ["Earnings Per Share"],
    "eps_diluted":      ["Diluted Earnings Per Share"],
}

# Reconcile targets: our computed subtotal -> Carson's reported-check model line
IS_CHECK_LINES = {
    "gross_profit":     "Gross Profit (reported)",
    "operating_income": "Operating Income (reported)",
    "pretax_income":    "Pretax Income (reported)",
    "net_income":       "Net Income (reported)",
}


def pull_detail_accounts(income_record, records=None, ticker=None):
    """Pull one year's detail lines. `records` is the full history — synonym
    resolution needs all years (HasData spans the whole history), so passing a
    single year weakens it to what that year happens to contain."""
    records = records if records is not None else [income_record]

    a = pull_aliased(STATEMENT, income_record, records, IS_ALIASES, ticker)

    # FMP's totalOtherIncomeExpensesNet INCLUDES interest income/expense. The model
    # shows interest on its own lines, so when that synonym wins, interest is netted
    # back out of Other Income — otherwise pretax income counts interest twice.
    # (Carson's built sheet does exactly this; the fallback synonym already excludes
    # interest, so it needs no netting.)
    other_rows = resolve_active(STATEMENT, records).get("Other Income (Expense)", [])
    if any(r["synonym"] == "totalOtherIncomeExpensesNet" for r in other_rows):
        a["other_income"] = a["other_income"] - a["interest_income"] + a["interest_expense"]

    # Other Operating Expenses is a cross-check line in Carson's map (no Detail
    # anchor), but the built statement still adds it into Total OpEx.
    other_opex = resolve_line_value(STATEMENT, "Other Operating Expenses", income_record, records)
    a["other_opex"] = other_opex if other_opex is not None else 0.0

    a["date"] = (income_record.get("fiscalYear")
                 or income_record.get("date")
                 or income_record.get("period"))
    return a


def compute_formula_lines(a):
    """Compute subtotals from detail accounts. 'a' = pulled detail accounts."""
    gross_profit = a["revenue"] - a["cogs"]
    total_opex = a["sga"] + a["rnd"] + a["other_opex"]
    operating_income = gross_profit - total_opex
    pretax = operating_income + a["interest_income"] - a["interest_expense"] + a["other_income"]
    net_income = pretax - a["income_tax"]
    return {
        "gross_profit": gross_profit,
        "total_operating_expenses": total_opex,
        "operating_income": operating_income,
        "pretax_income": pretax,
        "net_income": net_income,
    }


def reconcile_rows(income_record, computed, records=None):
    """Structured reconcile results — one row per line item. Persisted to check_results."""
    records = records if records is not None else [income_record]
    return [
        compare_line(line, computed.get(line),
                     resolve_line_value(STATEMENT, check_line, income_record, records))
        for line, check_line in IS_CHECK_LINES.items()
    ]


def reconcile(income_record, computed, records=None):
    """Compare our computed subtotals against FMP's reported values (display form)."""
    return to_display(reconcile_rows(income_record, computed, records), thousands=False)