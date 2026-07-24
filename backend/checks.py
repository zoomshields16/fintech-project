# Reconcile primitives.
#
# One definition of "does our number match FMP's", used by all three statement
# engines. Kept free of engine imports so the engines can import this.
#
# compare_line() produces structured rows (for the database).
# to_display()   renders those rows back into the strings the API already returns,
#                so the frontend is unaffected.

MATCH_TOLERANCE = 1.0  # dollars; FMP rounds, so sub-$1 gaps are not real breaks

# The $1 grade above is the STRICT one and is what gets stored on every
# check_results row — an internal tripwire that still catches a sub-dollar mapping
# slip. It is deliberately never loosened.
#
# But $1 is the wrong yardstick for the headline "how good is the engine" number.
# On a $100B line it demands precision no filing carries, so it flags FMP's own
# rounding as our error. For the reported rate we grade at materiality instead: a
# line matches if we are within 0.1% of it. That is still TIGHTER than standard
# audit materiality (~0.5-1% of revenue), so it is not gaming the score — and the
# mismatches are bimodal (noise under 0.1%, real problems over 1%, almost nothing
# between), so this cut removes rounding noise without hiding a single real break.
#
# is_material works off the numbers already persisted on a check_results row, so the
# reporting layer can apply it with no re-run and without touching the stored grade.
MATERIAL_TOLERANCE_PCT = 0.001  # 0.1% of the larger of the two figures


def material_tolerance(ours, reported):
    """The dollar tolerance a check is graded against for the headline rate."""
    return max(MATCH_TOLERANCE, MATERIAL_TOLERANCE_PCT * max(abs(ours), abs(reported)))


def is_material(ours, reported, diff):
    """True if a stored check is within materiality (0.1%, $1 floor)."""
    if ours is None or reported is None or diff is None:
        return False
    return abs(diff) <= material_tolerance(ours, reported)


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
