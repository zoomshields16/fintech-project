# Read/write layer over the raw-pull log.
#
# save_raw_pulls()  — append one PullBatch (a fetch event) plus its RawPull rows.
# load_recent_pull() — serve the newest complete batch for a ticker if it is
#                      fresh enough, so we skip the FMP (Financial Modeling Prep)
#                      API call entirely.
#
# Nothing here mutates an existing row: a new fetch appends a new batch, and old
# batches stay as history.

import json
from datetime import datetime, timezone, timedelta

from db import SessionLocal
from models import Company, PullBatch, RawPull

# Keys of get_financials() that come back as a list of per-year records.
STATEMENT_KEYS = ("income_statement", "cash_flow", "balance_sheet")

# A batch is only usable as a cache hit if all of these are present.
REQUIRED_KEYS = STATEMENT_KEYS + ("profile",)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_raw_pulls(ticker, company_name, data):
    """Append a fetch event and every record it returned."""
    session = SessionLocal()
    try:
        company = session.query(Company).filter_by(ticker=ticker).first()
        if not company:
            company = Company(ticker=ticker, company_name=company_name)
            session.add(company)
            session.flush()
        elif company_name:
            company.company_name = company_name

        batch = PullBatch(
            company_id=company.id,
            source="fmp",
            fetched_at=_utcnow(),
            status="pending",
        )
        session.add(batch)
        session.flush()

        for statement_type, payload in data.items():
            if not payload:
                continue
            if statement_type in STATEMENT_KEYS:
                # Per-year records: one row each, position preserved in seq.
                for i, record in enumerate(payload):
                    session.add(RawPull(
                        batch_id=batch.id,
                        statement_type=statement_type,
                        fiscal_date=record.get("date"),
                        seq=i,
                        raw_json=json.dumps(record),
                    ))
            else:
                # profile, enterprise_values, treasury_rates, etc. — store the
                # payload whole so it round-trips in exactly the shape FMP sent.
                session.add(RawPull(
                    batch_id=batch.id,
                    statement_type=statement_type,
                    fiscal_date=None,
                    seq=0,
                    raw_json=json.dumps(payload),
                ))

        # Mark complete only once everything we depend on actually landed.
        batch.status = "complete" if all(data.get(k) for k in REQUIRED_KEYS) else "partial"
        session.commit()
    finally:
        session.close()


def load_recent_pull(ticker, max_age_hours=24):
    """Return the newest complete batch for `ticker` as a dict shaped exactly
    like get_financials()'s return value, or None on a cache miss."""
    session = SessionLocal()
    try:
        company = session.query(Company).filter_by(ticker=ticker).first()
        if not company:
            return None

        cutoff = _utcnow() - timedelta(hours=max_age_hours)
        batch = (
            session.query(PullBatch)
            .filter(
                PullBatch.company_id == company.id,
                PullBatch.status == "complete",
                PullBatch.fetched_at >= cutoff,
            )
            .order_by(PullBatch.fetched_at.desc())
            .first()
        )
        if not batch:
            return None

        rows = (
            session.query(RawPull)
            .filter(RawPull.batch_id == batch.id)
            .order_by(RawPull.seq)
            .all()
        )

        data = {}
        for row in rows:
            payload = json.loads(row.raw_json)
            if row.statement_type in STATEMENT_KEYS:
                data.setdefault(row.statement_type, []).append(payload)
            else:
                data[row.statement_type] = payload

        return data
    finally:
        session.close()
