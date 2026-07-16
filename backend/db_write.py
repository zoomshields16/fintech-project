# Read/write layer over the fetch log.
#
# save_fetch()        — append one Fetch (a fetch event) plus its ApiResponse rows.
# load_recent_fetch() — serve the newest complete fetch for a ticker if it is
#                       fresh enough, so we skip the FMP (Financial Modeling Prep)
#                       API call entirely.
#
# Nothing here mutates an existing row: a new fetch appends new rows, and old
# fetches stay as history.
#tester
#tester 3.0

import json
from datetime import datetime, timezone, timedelta

from db import SessionLocal
from models import Company, Fetch, ApiResponse

# Keys of get_financials() that come back as a list of per-year records.
STATEMENT_KEYS = ("income_statement", "cash_flow", "balance_sheet")

# A fetch is only usable as a cache hit if all of these are present.
REQUIRED_KEYS = STATEMENT_KEYS + ("profile",)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_fetch(ticker, company_name, data):
    """Append a fetch event and every record it returned. Returns the new fetch id."""
    session = SessionLocal()
    try:
        company = session.query(Company).filter_by(ticker=ticker).first()
        if not company:
            company = Company(ticker=ticker, company_name=company_name)
            session.add(company)
            session.flush()
        elif company_name:
            company.company_name = company_name

        fetch = Fetch(
            company_id=company.id,
            source="fmp",
            fetched_at=_utcnow(),
            status="pending",
        )
        session.add(fetch)
        session.flush()

        for statement_type, payload in data.items():
            if not payload:
                continue
            if statement_type in STATEMENT_KEYS:
                # Per-year records: one row each, position preserved in year_position.
                for i, record in enumerate(payload):
                    session.add(ApiResponse(
                        fetch_id=fetch.id,
                        statement_type=statement_type,
                        fiscal_date=record.get("date"),
                        year_position=i,
                        response_json=json.dumps(record),
                    ))
            else:
                # profile, enterprise_values, treasury_rates, etc. — store the
                # payload whole so it round-trips in exactly the shape FMP sent.
                session.add(ApiResponse(
                    fetch_id=fetch.id,
                    statement_type=statement_type,
                    fiscal_date=None,
                    year_position=0,
                    response_json=json.dumps(payload),
                ))

        # Mark complete only once everything we depend on actually landed.
        fetch.status = "complete" if all(data.get(k) for k in REQUIRED_KEYS) else "partial"
        session.commit()
        return fetch.id  # read before close(); the instance expires afterwards
    finally:
        session.close()


def _fetch_to_data(session, fetch):
    """Reassemble a fetch's ApiResponse rows into get_financials()'s dict shape."""
    rows = (
        session.query(ApiResponse)
        .filter(ApiResponse.fetch_id == fetch.id)
        .order_by(ApiResponse.year_position)
        .all()
    )

    data = {}
    for row in rows:
        payload = json.loads(row.response_json)
        if row.statement_type in STATEMENT_KEYS:
            data.setdefault(row.statement_type, []).append(payload)
        else:
            data[row.statement_type] = payload
    return data


def load_fetch(fetch_id):
    """Rehydrate one fetch by id, regardless of age. Used to re-run derived work
    (like the reconcile checks) over data we already have — no network needed."""
    session = SessionLocal()
    try:
        fetch = session.query(Fetch).filter_by(id=fetch_id).first()
        return _fetch_to_data(session, fetch) if fetch else None
    finally:
        session.close()


def load_recent_fetch(ticker, max_age_hours=24):
    """Return the newest complete fetch for `ticker` as a dict shaped exactly
    like get_financials()'s return value, or None on a cache miss."""
    session = SessionLocal()
    try:
        company = session.query(Company).filter_by(ticker=ticker).first()
        if not company:
            return None

        cutoff = _utcnow() - timedelta(hours=max_age_hours)
        fetch = (
            session.query(Fetch)
            .filter(
                Fetch.company_id == company.id,
                Fetch.status == "complete",
                Fetch.fetched_at >= cutoff,
            )
            .order_by(Fetch.fetched_at.desc())
            .first()
        )
        if not fetch:
            return None

        return _fetch_to_data(session, fetch)
    finally:
        session.close()