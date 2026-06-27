# Carson's IS Synonym Map — logic is final, synonyms get added over time.
# Each "detail" line lists the FMP field names that mean that line.
# The code tries each synonym until one is found in the company's data.

DETAIL_ACCOUNTS = {
    "date": ["fiscalYear", "date", "period"],
    "revenue": ["revenue", "totalRevenue", "sales", "netSales"],
    "cogs": ["costOfRevenue", "costOfGoodsSold", "costOfSales"],
    "sga": ["sellingGeneralAndAdministrativeExpenses", "sellingAndMarketingExpenses",
            "generalAndAdministrativeExpenses", "sellingExpense"],
    "rnd": ["researchAndDevelopmentExpenses"],
    "other_opex": ["otherExpenses", "otherOperatingExpenses"],
    "interest_income": ["interestIncome"],
    "interest_expense": ["interestExpense"],
    "other_income": ["totalOtherIncomeExpensesNet"],
    "income_tax": ["incomeTaxExpense", "incomeTaxExpenseBenefit"],
    "eps": ["eps"],
    "eps_diluted": ["epsDiluted"],
}

# Reported values pulled ONLY to check our computed subtotals against.
CHECK_ACCOUNTS = {
    "gross_profit_reported": ["grossProfit"],
    "operating_income_reported": ["operatingIncome", "ebit"],
    "pretax_reported": ["incomeBeforeTax"],
    "net_income_reported": ["netIncome", "bottomLineNetIncome"],
    "ebitda_reported": ["ebitda"],
}


def find_value(record, synonyms):
    """Try each synonym name; return the first one present in the record."""
    for name in synonyms:
        if name in record and record[name] is not None:
            return record[name]
    return None

def pull_detail_accounts(income_record):
    """Pull every Direct Detail Account from one year's income statement."""
    result = {}
    for line_name, synonyms in DETAIL_ACCOUNTS.items():
        result[line_name] = find_value(income_record, synonyms)
    return result

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

def reconcile(income_record, computed):
    """Compare our computed subtotals against FMP's reported values."""
    checks = {
        "gross_profit": find_value(income_record, CHECK_ACCOUNTS["gross_profit_reported"]),
        "operating_income": find_value(income_record, CHECK_ACCOUNTS["operating_income_reported"]),
        "pretax_income": find_value(income_record, CHECK_ACCOUNTS["pretax_reported"]),
        "net_income": find_value(income_record, CHECK_ACCOUNTS["net_income_reported"]),
    }
    results = {}
    for line, reported in checks.items():
        ours = computed[line]
        if reported is None:
            results[line] = "no reported value"
        elif abs(ours - reported) < 1:
            results[line] = "MATCH"
        else:
            results[line] = f"MISMATCH (ours={ours}, reported={reported})"
    return results

if __name__ == "__main__":
    from fmp_test import get_financials

    data = get_financials("AAPL")
    latest_year = data["income_statement"][0]

    accounts = pull_detail_accounts(latest_year)
    formulas = compute_formula_lines(accounts)

    print("--- AAPL detail accounts ---")
    for line, value in accounts.items():
        print(f"{line}: {value}")

    print("\n--- AAPL computed subtotals ---")
    for line, value in formulas.items():
        print(f"{line}: {value}")

    checks = reconcile(latest_year, formulas)
    print("\n--- Reconciliation vs FMP reported ---")
    for line, status in checks.items():
        print(f"{line}: {status}")