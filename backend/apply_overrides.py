# Applies our mapping decisions on top of a freshly exported mappings.json.
#
#   python export_mappings.py "../reference/model 56.xlsm"
#   python apply_overrides.py
#
# ALWAYS run this second. export_mappings.py rebuilds mappings.json from Carson's
# workbook and overwrites whatever was there, so any edit made directly to the JSON
# is destroyed by the next export. That happened once already: three fixes worth
# ~4.5 percentage points of pass rate lived only in the JSON, and model 56's export
# would have silently wiped them.
#
# This file exists so those decisions are code rather than hand edits — versioned,
# reviewable, and reapplied identically every time a new workbook lands.
#
# Each override records WHY it exists, because every one of them is a place where we
# knowingly diverge from the workbook, and a future reader needs to know whether the
# divergence still applies.

import json
import sys
from pathlib import Path

MAPPINGS = Path(__file__).resolve().parent / "mappings.json"


def _find(rows, model_line, synonym):
    return next((r for r in rows
                 if r["model_line"] == model_line and r["synonym"] == synonym), None)


def _set_priority(rows, model_line, synonym, priority, label):
    row = _find(rows, model_line, synonym)
    if row is None:
        print(f"  SKIP  {label}: no row for {model_line} <- {synonym}")
        return False
    if row["priority"] == priority:
        print(f"  ok    {label}: already P{priority} in the workbook")
        return False
    print(f"  set   {label}: {synonym} P{row['priority']} -> P{priority}")
    row["priority"] = priority
    return True


def _add_synonym(rows, model_line, synonym, priority, notes, label):
    if _find(rows, model_line, synonym):
        print(f"  ok    {label}: workbook already maps {synonym}")
        return False
    template = next((r for r in rows if r["model_line"] == model_line), None)
    if template is None:
        print(f"  SKIP  {label}: no model line '{model_line}' to attach to")
        return False
    rows.append({**template, "synonym": synonym, "priority": priority, "notes": notes})
    print(f"  add   {label}: {model_line} <- {synonym} at P{priority}")
    return True


# Carson's reclass rows that push an NCI or discontinued-operations difference into
# Other Income (Expense). Two reasons these come out:
#
#   1. They are redundant. They exist to close the gap between our computed net
#      income and FMP's post-NCI `netIncome`. Override 2 below closes that same gap
#      globally by pointing the check at the pre-NCI field instead, so applying both
#      counts the adjustment twice — measured on HON, whose mismatches came out to
#      exactly the reclass amounts.
#   2. They break the line above. Other Income sits ABOVE pretax income, but NCI and
#      discontinued operations sit BELOW the tax line. So the reclass fixes
#      net_income and breaks pretax_income by the identical amount — it moves the
#      error up one line rather than removing it.
#
# Every income-statement reclass in model 56 that targets Other Income exists to
# reconcile built IS net income against the cash-flow top line — the exact
# comparison override 2 repairs globally — so the whole column comes out rather
# than a hand-picked subset.
#
# Measured on the full Nasdaq-100: keeping them all 90.3%, dropping only the ones
# whose reason names NCI 90.6%, dropping the column 90.8%.
RECLASS_TARGET = "Other Income (Expense)"


def _add_reclass(reclasses, row, label):
    """Append a reclass unless an identical ticker/year/line is already present."""
    if any(r["ticker"] == row["ticker"] and r["fiscal_year"] == row["fiscal_year"]
           and r.get("from_line") == row.get("from_line") for r in reclasses):
        print(f"  ok    {label}: workbook already carries it")
        return False
    reclasses.append(row)
    print(f"  add   {label}: {row['ticker']} FY{row['fiscal_year']} "
          f"{row['from_line']} -{row['amount']:,.0f}")
    return True


# WDC's FY2026 record arrives with two separate FMP errors. Both are corrections to one
# company-year, so they belong in the reclass table — the master dictionary stays
# universal, which is what lets the same mapping serve all 101 companies.
#
# Measured before writing them: our assets already match FMP's reported total exactly.
# Only liabilities (+$1,774M) and equity (+$4,997M) are overstated, and removing exactly
# those two amounts makes the balance sheet tie to the dollar.
#
#   1. accountPayables is double-counted. FMP's own otherCurrentLiabilities ($3,188M)
#      plus shortTermDebt ($1,052M) equal its reported totalCurrentLiabilities ($4,240M)
#      on the nose, so accountPayables ($1,774M) is already inside otherCurrentLiabilities
#      and we add it a second time.
#   2. preferredStock is reported as $13,861M, which is exactly totalAssets — impossible,
#      and the field Carson maps into Common Stock / APIC. FMP supplies no equity detail
#      at all for this record (commonStock, retainedEarnings, APIC and AOCI are all zero),
#      only the $8,864M total, so the line is overstated by $4,997M.
#
# Scoped to FY2026 rather than the ticker, because this is a bad filing record and not a
# permanent trait of the company — see [Carson owns the finance logic].
WDC_RECLASSES = [
    {"ticker": "WDC", "fiscal_year": 2026, "statement": "BS",
     "source_field": "accountPayables",
     "from_line": "Accounts Payable", "to_line": "FMP double-count, already inside otherCurrentLiabilities",
     "amount": 1774000000.0,
     "reason": "FMP FY2026: otherCurrentLiabilities 3,188M + shortTermDebt 1,052M equal "
               "reported totalCurrentLiabilities 4,240M exactly, so accountPayables "
               "1,774M is already included and we count it twice."},
    {"ticker": "WDC", "fiscal_year": 2026, "statement": "BS",
     "source_field": "preferredStock",
     "from_line": "Common Stock / APIC", "to_line": "FMP preferredStock reported as total assets",
     "amount": 4997000000.0,
     "reason": "FMP FY2026: preferredStock reported as 13,861M, identical to totalAssets "
               "and impossible. No equity components are supplied, only the 8,864M total, "
               "so Common Stock / APIC is overstated by 4,997M."},
]


def apply_overrides(path=MAPPINGS):
    data = json.loads(Path(path).read_text())
    IS = data["statements"]["income_statement"]
    CF = data["statements"]["cash_flow"]
    BS = data["statements"]["balance_sheet"]
    changed = 0

    # 1. Total equity: FMP's totalStockholdersEquity EXCLUDES non-controlling
    #    interest while our subtotal includes it. totalEquity is the including-NCI
    #    figure and is the like-for-like comparison. Proof our subtotal was right:
    #    total_lae (which maps to the including-NCI total) passed for the same years.
    print("[1] total equity target")
    changed += _set_priority(BS["master"], "Total Equity (reported)", "totalEquity", 1, "total equity")
    changed += _set_priority(BS["master"], "Total Equity (reported)", "totalStockholdersEquity", 2, "total equity")

    # 2. Net income: our figure is pretax - tax, i.e. consolidated and PRE-NCI.
    #    FMP's `netIncome` is post-NCI, so the two are not comparable for companies
    #    with minority interests. netIncomeFromContinuingOperations is the pre-NCI
    #    field. Carson approved this approach over his per-ticker reclass approach.
    print("[2] net income target (pre-NCI)")
    changed += _set_priority(IS["master"], "Net Income (reported)", "netIncome", 2, "net income")
    changed += _set_priority(IS["master"], "Net Income (reported)", "bottomLineNetIncome", 3, "net income")
    changed += _add_synonym(IS["master"], "Net Income (reported)", "netIncomeFromContinuingOperations", 1,
                            "Pre-NCI consolidated net income - matches our computed pretax - tax",
                            "net income")

    # 3. Preferred stock issuance lands in financing activities but was unmapped,
    #    so financing_cf and net_change_cash came up short by exactly that amount.
    #    Other Financing Activities is a Detail line, so same-priority synonyms sum.
    print("[3] preferred stock issuance")
    changed += _add_synonym(CF["master"], "Other Financing Activities", "netPreferredStockIssuance", 1,
                            "Sums with otherFinancingActivities (Detail line, ties are additive)",
                            "preferred stock")

    # 4. Drop the now-redundant NCI/discontinued-ops reclasses (see note above).
    print("[4] redundant NCI reclasses")
    before = len(IS["reclasses"])
    IS["reclasses"] = [r for r in IS["reclasses"] if r["to_line"] != RECLASS_TARGET]
    dropped = before - len(IS["reclasses"])
    if dropped:
        print(f"  drop  {dropped} of {before} income-statement reclasses "
              f"(superseded by override 2)")
        changed += dropped
    else:
        print("  ok    none present")

    # 5. Drop TSLA FY2021's investing plug. The workbook adds +$1.5B to Investing
    #    because "digital-asset purchases sit in FMP components but net differently
    #    in its reported investing total". FMP has since restated: its components now
    #    sum to its reported total exactly (-6,514M property and equipment, -132M
    #    purchases of investments, -1,222M other = -7,868M). The plug is therefore
    #    added on top of a figure that already ties, and it is the entire reason our
    #    investing cash flow and net change in cash break for that year.
    #
    #    Scoped to the one row rather than the whole "components don't tie; align"
    #    family, because the others still correspond to real gaps. Revisit the rest
    #    if FMP restates again — they fail the same way once upstream data is fixed.
    print("[5] stale TSLA FY2021 investing plug")
    before = len(CF["reclasses"])
    CF["reclasses"] = [r for r in CF["reclasses"]
                       if not (r["ticker"] == "TSLA" and r["fiscal_year"] == 2021
                               and r["to_line"] == "Investing")]
    dropped = before - len(CF["reclasses"])
    if dropped:
        print(f"  drop  {dropped} TSLA FY2021 investing reclass (FMP components now tie)")
        changed += dropped
    else:
        print("  ok    none present")

    # 6. WDC FY2026's two FMP errors (see WDC_RECLASSES above for the measurements).
    print("[6] WDC FY2026 corrupt balance sheet record")
    for row in WDC_RECLASSES:
        changed += _add_reclass(BS["reclasses"], row, "WDC FY2026")

    Path(path).write_text(json.dumps(data, indent=1))
    print(f"\n{changed} override(s) applied to {path}")
    return changed


if __name__ == "__main__":
    apply_overrides(sys.argv[1] if len(sys.argv) > 1 else MAPPINGS)
