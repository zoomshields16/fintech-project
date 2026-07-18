# Restatement detection.
#
# Compares two fetches of the same company field by field and records anything
# FMP now reports differently than it did before.
#
# This asks a different question than check_runner. check_runner asks whether OUR
# math is right, within one fetch. This asks whether THEIR data changed, across
# fetches — and it reads the raw stored responses, before any mapping, because the
# question is what FMP said rather than what we made of it.
#
# Only possible because api_responses is append-only: a pipeline that overwrote
# each fetch would have destroyed the evidence.

from db import SessionLocal
from models import Company, Fetch, Restatement
from db_write import load_fetch

STATEMENTS = ("income_statement", "balance_sheet", "cash_flow")

# Values arrive from JSON as whole numbers; anything smaller than this is float
# noise rather than a real change.
CHANGE_TOLERANCE = 0.01


def _comparable(value):
    """Numeric values only — booleans and strings are metadata, not figures."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _diff_fetches(old_data, new_data):
    """Yield (statement_type, fiscal_date, field, old, new) for every changed figure.

    Only fiscal years present in BOTH fetches are compared. A newly appearing year
    is a normal filing, not a restatement, so it is not reported here.
    """
    for statement in STATEMENTS:
        old_years = {r.get("date"): r for r in (old_data.get(statement) or []) if r.get("date")}
        new_years = {r.get("date"): r for r in (new_data.get(statement) or []) if r.get("date")}

        for fiscal_date in sorted(set(old_years) & set(new_years), reverse=True):
            old_record, new_record = old_years[fiscal_date], new_years[fiscal_date]
            for field in sorted(set(old_record) | set(new_record)):
                old_value, new_value = old_record.get(field), new_record.get(field)
                if not (_comparable(old_value) and _comparable(new_value)):
                    continue
                if abs(new_value - old_value) > CHANGE_TOLERANCE:
                    yield statement, fiscal_date, field, float(old_value), float(new_value)


def _recent_complete_fetches(session, ticker, limit=2):
    return (
        session.query(Fetch)
        .join(Company, Company.id == Fetch.company_id)
        .filter(Company.ticker == ticker, Fetch.status == "complete")
        .order_by(Fetch.id.desc())
        .limit(limit)
        .all()
    )


def detect_restatements(ticker, fetch_id=None, prior_fetch_id=None):
    """Compare two fetches of `ticker` and record what changed. Returns rows written.

    With no ids, compares the two most recent complete fetches. A company with only
    one fetch has nothing to compare against and returns 0.

    Re-running on the same pair replaces the previous findings rather than
    duplicating them, so this is safe to call repeatedly.
    """
    session = SessionLocal()
    try:
        if fetch_id is None or prior_fetch_id is None:
            recent = _recent_complete_fetches(session, ticker, limit=2)
            if len(recent) < 2:
                return 0
            fetch_id, prior_fetch_id = recent[0].id, recent[1].id

        fetch = session.query(Fetch).filter_by(id=fetch_id).first()
        if fetch is None:
            return 0

        old_data, new_data = load_fetch(prior_fetch_id), load_fetch(fetch_id)
        if not old_data or not new_data:
            return 0

        # Idempotent: clear any earlier findings for this exact pair.
        (session.query(Restatement)
         .filter(Restatement.fetch_id == fetch_id,
                 Restatement.prior_fetch_id == prior_fetch_id)
         .delete())

        written = 0
        for statement, fiscal_date, field, old_value, new_value in _diff_fetches(old_data, new_data):
            session.add(Restatement(
                company_id=fetch.company_id,
                fetch_id=fetch_id,
                prior_fetch_id=prior_fetch_id,
                statement_type=statement,
                fiscal_date=fiscal_date,
                field=field,
                old_value=old_value,
                new_value=new_value,
                delta=new_value - old_value,
            ))
            written += 1

        session.commit()
        return written
    finally:
        session.close()
