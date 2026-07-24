# Read-only health queries for the pipeline.
#
# Everything the pipeline learns already lands in the database — check_results,
# restatements, pipeline_runs. The problem this module solves is that none of it
# comes to you: it sits there until someone opens a database browser and thinks
# to look. These functions assemble that state into something an endpoint can
# hand to a page.
#
# Read-only except mark_reviewed(), which is the one piece of triage state.
#
# Every rate here is measured against the LATEST complete fetch per company, not
# every fetch ever stored. A company fetched ten times would otherwise count ten
# times toward the pass rate, and old fetches taken before a mapping fix would
# drag down a number meant to describe the engine as it stands today.

from datetime import datetime, timezone

from sqlalchemy import text

from checks import MATCH_TOLERANCE, MATERIAL_TOLERANCE_PCT
from db import SessionLocal
from models import Restatement

# Matches the default staleness threshold in refresh_stale.py. Kept as its own
# constant because this module only reports; it never triggers a refresh.
STALE_AFTER_DAYS = 7

# Earliest fiscal year the headline rate grades. Carson's reclass table does not
# touch 2016, so grading it holds the engine to a spec that was never written for
# those years; the failures there are un-curated FMP data, not mapping errors.
# fiscal_date is stored as 'YYYY-MM-DD', so a string compare on the year works.
EARLIEST_GRADED_YEAR = "2017"
_YEAR_FILTER = f"AND substr(cr.fiscal_date, 1, 4) >= '{EARLIEST_GRADED_YEAR}'"

# A check counts as a MATCH for the headline rate if it either cleared the strict
# grade or is within materiality (0.1%, $1 floor). Built from the constants in
# checks.py so the threshold has one home. NO_REPORTED_VALUE rows have a NULL diff
# and so fall through to non-match, same as the strict grade. SQLite MAX(a, b) is
# the scalar (two-argument) form.
_MATERIAL_MATCH = (
    f"(cr.status = 'MATCH' OR (cr.diff IS NOT NULL AND ABS(cr.diff) <= "
    f"MAX({MATCH_TOLERANCE}, {MATERIAL_TOLERANCE_PCT} * "
    f"MAX(ABS(cr.ours), ABS(cr.reported)))))"
)

# Common table expression reused by the check queries below.
_LATEST_FETCHES = """
WITH latest AS (
    SELECT company_id, MAX(id) AS fetch_id
    FROM fetches
    WHERE status = 'complete'
    GROUP BY company_id
)
"""


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.isoformat() if value else None


def _rate(matched, total):
    return round(100.0 * matched / total, 1) if total else None


def recent_runs(limit=10):
    """The last N scheduled-job executions, newest first."""
    session = SessionLocal()
    try:
        rows = session.execute(text("""
            SELECT id, job_name, started_at, finished_at, status,
                   tickers_attempted, tickers_succeeded, tickers_failed,
                   checks_written, restatements_found, notes
            FROM pipeline_runs
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return [dict(row) for row in rows]
    finally:
        session.close()


def coverage():
    """How much of the universe is loaded, and how fresh it is."""
    session = SessionLocal()
    try:
        row = session.execute(text("""
            SELECT COUNT(*) AS companies,
                   SUM(CASE WHEN last_complete IS NULL THEN 1 ELSE 0 END) AS never_fetched,
                   MAX(last_complete) AS newest_fetch,
                   MIN(last_complete) AS oldest_fetch
            FROM (
                SELECT c.id,
                       (SELECT MAX(f.fetched_at) FROM fetches f
                         WHERE f.company_id = c.id AND f.status = 'complete') AS last_complete
                FROM companies c
            ) t
        """)).mappings().first()

        cutoff = _utcnow().timestamp() - STALE_AFTER_DAYS * 86400
        stale = session.execute(text("""
            SELECT c.ticker,
                   (SELECT MAX(f.fetched_at) FROM fetches f
                     WHERE f.company_id = c.id AND f.status = 'complete') AS last_complete
            FROM companies c
        """)).mappings().all()

        stale_tickers = []
        for entry in stale:
            last = entry["last_complete"]
            if last is None:
                continue
            # SQLite hands back a string; Postgres a datetime.
            if isinstance(last, str):
                last = datetime.fromisoformat(last)
            if last.timestamp() < cutoff:
                stale_tickers.append(entry["ticker"])

        return {
            "companies": row["companies"] or 0,
            "never_fetched": row["never_fetched"] or 0,
            "stale_count": len(stale_tickers),
            "stale_after_days": STALE_AFTER_DAYS,
            "stale_tickers": sorted(stale_tickers),
            "newest_fetch": str(row["newest_fetch"]) if row["newest_fetch"] else None,
            "oldest_fetch": str(row["oldest_fetch"]) if row["oldest_fetch"] else None,
        }
    finally:
        session.close()


def check_summary():
    """Overall reconcile pass rate across the latest fetch of every company.

    The headline `pass_rate` is graded at materiality (0.1%) over 2017+. The strict
    $1 grade is kept alongside as `strict_pass_rate` so the tripwire stays visible
    and a regression in it is never masked by the looser headline."""
    session = SessionLocal()
    try:
        row = session.execute(text(_LATEST_FETCHES + f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN {_MATERIAL_MATCH} THEN 1 ELSE 0 END) AS matched,
                   SUM(CASE WHEN cr.status = 'MATCH' THEN 1 ELSE 0 END) AS strict_matched,
                   SUM(CASE WHEN NOT {_MATERIAL_MATCH}
                            AND cr.status <> 'NO_REPORTED_VALUE'
                            THEN 1 ELSE 0 END) AS mismatched,
                   SUM(CASE WHEN cr.status = 'NO_REPORTED_VALUE' THEN 1 ELSE 0 END) AS no_value
            FROM latest l
            JOIN check_results cr ON cr.fetch_id = l.fetch_id
            WHERE 1=1 {_YEAR_FILTER}
        """)).mappings().first()

        total = row["total"] or 0
        matched = row["matched"] or 0
        return {
            "total_checks": total,
            "matched": matched,
            "mismatched": row["mismatched"] or 0,
            "no_reported_value": row["no_value"] or 0,
            "pass_rate": _rate(matched, total),
            "strict_pass_rate": _rate(row["strict_matched"] or 0, total),
            "graded_from_year": int(EARLIEST_GRADED_YEAR),
            "materiality_pct": MATERIAL_TOLERANCE_PCT * 100,
        }
    finally:
        session.close()


def company_pass_rates(limit=None, worst_first=True):
    """Per-company pass rate on that company's latest fetch."""
    session = SessionLocal()
    try:
        rows = session.execute(text(_LATEST_FETCHES + f"""
            SELECT c.ticker, c.company_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN {_MATERIAL_MATCH} THEN 1 ELSE 0 END) AS matched
            FROM latest l
            JOIN companies c ON c.id = l.company_id
            JOIN check_results cr ON cr.fetch_id = l.fetch_id
            WHERE 1=1 {_YEAR_FILTER}
            GROUP BY c.ticker, c.company_name
        """)).mappings().all()

        result = [{
            "ticker": row["ticker"],
            "company_name": row["company_name"],
            "total_checks": row["total"],
            "matched": row["matched"],
            "pass_rate": _rate(row["matched"], row["total"]),
        } for row in rows]

        result.sort(key=lambda r: (r["pass_rate"] if r["pass_rate"] is not None else 0,
                                   r["ticker"]),
                    reverse=not worst_first)
        return result[:limit] if limit else result
    finally:
        session.close()


def worst_line_items(limit=12):
    """Which model lines fail most often — where a mapping fix would pay off most."""
    session = SessionLocal()
    try:
        rows = session.execute(text(_LATEST_FETCHES + f"""
            SELECT cr.line_item, cr.statement_type,
                   COUNT(*) AS total,
                   SUM(CASE WHEN NOT {_MATERIAL_MATCH}
                            AND cr.status <> 'NO_REPORTED_VALUE'
                            THEN 1 ELSE 0 END) AS mismatches,
                   COUNT(DISTINCT l.company_id) AS companies
            FROM latest l
            JOIN check_results cr ON cr.fetch_id = l.fetch_id
            WHERE 1=1 {_YEAR_FILTER}
            GROUP BY cr.line_item, cr.statement_type
            HAVING SUM(CASE WHEN NOT {_MATERIAL_MATCH}
                            AND cr.status <> 'NO_REPORTED_VALUE'
                            THEN 1 ELSE 0 END) > 0
            ORDER BY mismatches DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

        return [{
            "line_item": row["line_item"],
            "statement_type": row["statement_type"],
            "total_checks": row["total"],
            "mismatches": row["mismatches"],
            "companies": row["companies"],
            "fail_rate": _rate(row["mismatches"], row["total"]),
        } for row in rows]
    finally:
        session.close()


def restatements(ticker=None, only_unreviewed=True, limit=100):
    """Restated figures, newest first. The reviewed flag is what makes this a
    queue you can work down rather than a pile that only grows."""
    session = SessionLocal()
    try:
        where = []
        params = {"limit": limit}
        if only_unreviewed:
            where.append("r.reviewed = 0")
        if ticker:
            where.append("c.ticker = :ticker")
            params["ticker"] = ticker.upper()
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        rows = session.execute(text(f"""
            SELECT r.id, c.ticker, c.company_name, r.statement_type, r.fiscal_date,
                   r.field, r.old_value, r.new_value, r.delta,
                   r.detected_at, r.reviewed, r.reviewed_at,
                   r.fetch_id, r.prior_fetch_id
            FROM restatements r
            JOIN companies c ON c.id = r.company_id
            {clause}
            ORDER BY r.id DESC
            LIMIT :limit
        """), params).mappings().all()

        return [{
            **dict(row),
            "detected_at": str(row["detected_at"]) if row["detected_at"] else None,
            "reviewed_at": str(row["reviewed_at"]) if row["reviewed_at"] else None,
            "reviewed": bool(row["reviewed"]),
            # Percent move on the figure itself — a $2 change on a $90B line is
            # rounding; a 40% change is a real restatement worth reading.
            "pct_change": (round(100.0 * row["delta"] / abs(row["old_value"]), 2)
                           if row["old_value"] else None),
        } for row in rows]
    finally:
        session.close()


def restatement_counts(ticker=None):
    """Unreviewed / total, for a badge."""
    session = SessionLocal()
    try:
        params = {}
        clause = ""
        if ticker:
            clause = "WHERE c.ticker = :ticker"
            params["ticker"] = ticker.upper()

        row = session.execute(text(f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN r.reviewed = 0 THEN 1 ELSE 0 END) AS unreviewed,
                   COUNT(DISTINCT r.company_id) AS companies
            FROM restatements r
            JOIN companies c ON c.id = r.company_id
            {clause}
        """), params).mappings().first()

        return {
            "total": row["total"] or 0,
            "unreviewed": row["unreviewed"] or 0,
            "companies_affected": row["companies"] or 0,
        }
    finally:
        session.close()


def mark_reviewed(ids, reviewed=True):
    """Flip the triage flag on specific findings. Returns rows changed."""
    if not ids:
        return 0
    session = SessionLocal()
    try:
        changed = (session.query(Restatement)
                   .filter(Restatement.id.in_(ids))
                   .update({"reviewed": 1 if reviewed else 0,
                            "reviewed_at": _utcnow() if reviewed else None},
                           synchronize_session=False))
        session.commit()
        return changed
    finally:
        session.close()


def full_status():
    """Everything the status page needs, in one round trip."""
    counts = restatement_counts()
    rates = company_pass_rates(worst_first=True)
    return {
        "generated_at": _iso(_utcnow()),
        "coverage": coverage(),
        "checks": check_summary(),
        "restatements": counts,
        "unreviewed_restatements": restatements(only_unreviewed=True, limit=50),
        "worst_line_items": worst_line_items(),
        "worst_companies": rates[:15],
        "best_companies": rates[-10:][::-1],
        "recent_runs": recent_runs(limit=8),
    }