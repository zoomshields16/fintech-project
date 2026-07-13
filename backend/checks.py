# Reconcile primitives.
#
# One definition of "does our number match FMP's", used by all three statement
# engines. Kept free of engine imports so the engines can import this.
#
# compare_line() produces structured rows (for the database).
# to_display()   renders those rows back into the strings the API already returns,
#                so the frontend is unaffected.

MATCH_TOLERANCE = 1.0  # dollars; FMP rounds, so sub-$1 gaps are not real breaks

MATCH = "MATCH"
MISMATCH = "MISMATCH"
NO_REPORTED_VALUE = "NO_REPORTED_VALUE"


def compare_line(line_item, ours, reported):
    """One assertion: our computed subtotal vs FMP's reported value."""
    if ours is None or reported is None:
        return {
            "line_item": line_item,
            "ours": ours,
            "reported": reported,
            "status": NO_REPORTED_VALUE,
            "diff": None,
        }

    diff = ours - reported
    return {
        "line_item": line_item,
        "ours": ours,
        "reported": reported,
        "status": MATCH if abs(diff) < MATCH_TOLERANCE else MISMATCH,
        "diff": diff,
    }


def to_display(rows, thousands=True):
    """Back-compat: the {line_item: string} map main.py already hands the frontend."""
    out = {}
    for r in rows:
        if r["status"] == NO_REPORTED_VALUE:
            out[r["line_item"]] = "no reported value"
        elif r["status"] == MATCH:
            out[r["line_item"]] = "MATCH"
        elif thousands:
            out[r["line_item"]] = (
                f"MISMATCH (ours={r['ours']:,.0f}, reported={r['reported']:,.0f})"
            )
        else:
            out[r["line_item"]] = (
                f"MISMATCH (ours={r['ours']}, reported={r['reported']})"
            )
    return out
