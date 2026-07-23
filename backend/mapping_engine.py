# The generic mapping engine — a Python port of the resolution logic in Carson's
# workbook. It knows nothing about revenue, SG&A, or balance sheets; it only reads
# mappings.json and follows the rules there. A new model workbook changes the data,
# not this file.
#
# Excel resolves a model line with three formulas. Ported literally:
#
#   IsCandidate = use_type in (Detail, Duplicate/Backup)
#                 AND the model line has a Priority-1 Detail row anchoring it
#   HasData     = SUMPRODUCT(ABS(all year columns)) > 0
#                 i.e. the synonym is non-zero in AT LEAST ONE year, judged across
#                 the whole 10-year history, NOT year by year
#   Active      = IsCandidate AND HasData
#                 AND priority == MIN(priority among candidates that have data)
#
# Two consequences worth stating out loud, because the old engine got both wrong:
#
#   1. TIES ARE ADDITIVE. MINIFS picks the minimum priority, and EVERY row at that
#      priority is Active. A model line's value is the SUM of its active synonyms.
#      SG&A falls back to generalAndAdministrative + sellingAndMarketing TOGETHER;
#      taking only the first drops half the expense.
#
#   2. HasData IS PER-TICKER, NOT PER-YEAR. The winning synonym is chosen once from
#      the full history, then used for every year. Choosing per-year lets a company
#      silently switch synonyms mid-history, which makes older years disagree with
#      newer ones — exactly the drift the reconcile checks surfaced.

import json
from functools import lru_cache
from pathlib import Path

MAPPINGS_PATH = Path(__file__).resolve().parent / "mappings.json"

DETAIL = "Detail"
BACKUP = "Duplicate/Backup"
PULLABLE = (DETAIL, BACKUP)          # only these are ever pulled into the model
# The workbook's other use_types are "Total/Check" and "Check". They are never
# pulled into the model — they are the reported figures we reconcile against, and
# checks.py reads them straight from mappings.json rather than through here.


@lru_cache(maxsize=1)
def load_mappings():
    return json.loads(MAPPINGS_PATH.read_text())


def fiscal_year(record):
    """FMP gives calendarYear on most records; fall back to the date's year."""
    cy = record.get("calendarYear")
    if cy:
        return int(cy)
    date = record.get("date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def _value(record, field):
    v = record.get(field)
    return 0.0 if v is None else float(v)


def _is_candidate(row, rows_for_line):
    """Pullable row, on a model line that has a Priority-1 Detail row anchoring it.

    Some lines in Carson's map have no Priority-1 row at all (the cash-flow working
    capital splits start at Priority 2, because Priority 1 is the aggregate on a
    different model line). The anchor requirement only means something when an
    anchor exists, so for those lines any pullable row is a candidate.
    """
    if row["use_type"] not in PULLABLE:
        return False
    has_anchor = any(r["use_type"] == DETAIL and r["priority"] == 1 for r in rows_for_line)
    return has_anchor or all(r["priority"] != 1 for r in rows_for_line)


def _has_data(row, records):
    """Non-zero in at least one year, judged across the whole history."""
    return sum(abs(_value(rec, row["synonym"])) for rec in records) > 0


def _by_model_line(master):
    grouped = {}
    for row in master:
        grouped.setdefault(row["model_line"], []).append(row)
    return grouped


def resolve_active(statement, records):
    """Which synonyms win for this company, per model line.

    Returns {model_line: [row, ...]} — a list because ties at the same priority are
    all active and get summed.
    """
    master = load_mappings()["statements"][statement]["master"]
    active = {}

    for model_line, rows in _by_model_line(master).items():
        candidates = [
            r for r in rows
            if _is_candidate(r, rows) and _has_data(r, records)
        ]
        if not candidates:
            active[model_line] = []
            continue

        best = min(r["priority"] for r in candidates)
        active[model_line] = [r for r in candidates if r["priority"] == best]

    return active


def resolve_line_value(statement, model_line, record, records):
    """One year's value for a single model line, whatever its use type.

    Used for the lines the model reads but never sums with others: reported totals
    (reconcile targets), memo lines, and supporting figures. Lowest priority with
    data wins outright — a reported total is a single figure, so ties don't add.
    Returns None when no synonym has data, so callers can tell "not reported"
    apart from a real zero.
    """
    master = load_mappings()["statements"][statement]["master"]
    rows = [r for r in master if r["model_line"] == model_line]
    with_data = sorted((r for r in rows if _has_data(r, records)), key=lambda r: r["priority"])
    return _value(record, with_data[0]["synonym"]) if with_data else None


def pull_aliased(statement, record, records, aliases, ticker=None):
    """One year's values, translated to the app's snake_case keys.

    `aliases` maps each snake_case key to the Carson model line(s) that feed it —
    a list, because the app displays some of his lines combined (e.g. our
    "other_current_liab" is his Other Current Liabilities + Deferred Revenue +
    Income Taxes Payable). Reclass adjustments land on the model line, so they are
    applied here before the fold.
    """
    active = resolve_active(statement, records)
    adjustments = reclass_adjustments(statement, ticker) if ticker else {}
    year = fiscal_year(record)

    out = {}
    for key, model_lines in aliases.items():
        total = 0.0
        for line in model_lines:
            total += sum(_value(record, r["synonym"]) for r in active.get(line, []))
            total += adjustments.get((year, line), 0.0)
        out[key] = total
    return out


def reclass_adjustments(statement, ticker):
    """{(fiscal_year, target): summed amount} for this ticker.

    `target` is a model line for IS/BS. For CF the workbook reclasses between
    SECTIONS (Operating/Investing/Financing/FX) rather than lines, so the target is
    a section name there — the caller decides what to do with it.

    A reclass is a transfer, so the amount is added to `to_line` AND subtracted from
    `from_line`. The workbook encodes direction by which of the two columns holds a
    real model line: a gross-up names only the target and describes the source in
    prose ("FMP unallocated non-current assets"), while a removal names only the
    source and describes the target in prose ("FMP over-listed NCL"). Prose resolves
    to no model line and is silently dropped, which is exactly what makes one a
    gross-up and the other a removal. Model 62 splits 69/47 between the two and has
    no row where both columns are real.

    Honouring `from_line` is what makes removals work at all; before it, the 47
    removal rows were parsed, stored, and then quietly ignored.

    Multiple rows can hit the same target in the same year, so they are summed.
    """
    reclasses = load_mappings()["statements"][statement]["reclasses"]
    out = {}
    for r in reclasses:
        if r["ticker"] != ticker:
            continue
        year = r["fiscal_year"]
        for line, sign in ((r.get("to_line"), 1.0), (r.get("from_line"), -1.0)):
            if not line:
                continue
            out[(year, line)] = out.get((year, line), 0.0) + sign * r["amount"]
    return out
