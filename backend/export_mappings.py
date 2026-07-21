# Exports Carson's mapping spec out of the .xlsm into mappings.json.
#
#   python export_mappings.py "../reference/model 56.xlsm"
#   python apply_overrides.py        <- ALWAYS run this second
#
# Run this whenever a new model workbook lands. Nothing is transcribed by hand — the
# workbook is the source of truth and mappings.json is a build artifact of it.
#
# This script REBUILDS mappings.json from scratch, discarding whatever was there.
# Our own mapping decisions (the pre-NCI net income target, preferred stock
# issuance, the dropped Other-Income reclasses) are therefore NOT preserved by it —
# apply_overrides.py reapplies them, and skipping that step silently costs about
# half a point of reconcile pass rate with no error to warn you.
#
# The .xlsm is gitignored (an API key is embedded in it), so mappings.json is what
# actually ships: it carries the spec without carrying the secret.
#
# Only the *rules* are exported (model line, synonym, use type, priority). The
# Active / HasData / IsCandidate columns are deliberately NOT exported — those are
# live per-ticker formulas, and mapping_engine.py recomputes them from real FMP data
# at runtime. Freezing them would bake one company's shape into every other company:
# the workbook ships with PEP loaded, PEP has no R&D, so a frozen Active would zero
# out R&D for every company that does have it.

import json
import sys
from pathlib import Path

import openpyxl

SHEETS = {
    "income_statement": "IS_Map",
    "balance_sheet": "BS_Map",
    "cash_flow": "CF_Map",
}

MASTER_COLS = ["statement", "section", "model_line", "synonym", "use_type", "priority", "active", "notes"]
RECLASS_COLS = ["ticker", "fiscal_year", "statement", "source_field", "from_line", "to_line", "amount", "reason"]

OUT_PATH = Path(__file__).resolve().parent / "mappings.json"


def _find_header_rows(ws):
    """Locate the two tables: the master map starts at 'Statement', reclasses at 'Ticker'."""
    master_hdr = reclass_hdr = None
    for i in range(1, ws.max_row + 1):
        first = ws.cell(row=i, column=1).value
        first = str(first).strip() if first else ""
        if first == "Statement" and master_hdr is None:
            master_hdr = i
        elif first == "Ticker" and reclass_hdr is None:
            reclass_hdr = i
    return master_hdr, reclass_hdr


def export(xlsm_path):
    wb = openpyxl.load_workbook(xlsm_path, data_only=True, read_only=True)
    out = {"source": Path(xlsm_path).name, "statements": {}}

    for statement, sheet_name in SHEETS.items():
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=8, values_only=True))
        master_hdr, reclass_hdr = _find_header_rows(ws)

        master = []
        for r in rows[master_hdr:reclass_hdr - 1]:
            if not r[2] or not r[3]:  # a real mapping row needs a model line and a synonym
                continue
            row = dict(zip(MASTER_COLS, r))
            row.pop("active")  # recomputed per ticker at runtime, never frozen
            row["priority"] = int(row["priority"]) if row["priority"] is not None else 99
            master.append(row)

        reclasses = []
        for r in rows[reclass_hdr:]:
            if not r[0] or r[6] in (None, ""):  # need a ticker and an amount
                continue
            row = dict(zip(RECLASS_COLS, r))
            row["fiscal_year"] = int(row["fiscal_year"])
            row["amount"] = float(row["amount"])
            reclasses.append(row)

        out["statements"][statement] = {"master": master, "reclasses": reclasses}
        print(f"{sheet_name:<7} -> {statement:<17} {len(master):>3} mapping rows, {len(reclasses):>3} reclasses")

    OUT_PATH.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python export_mappings.py "<path to model.xlsm>"')
    export(sys.argv[1])
