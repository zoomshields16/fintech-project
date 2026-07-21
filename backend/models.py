# SQLAlchemy models.
#
# Shape: companies -> fetches -> api_responses
#
# A Fetch is one fetch event: "we went to FMP for AAPL at this instant."
# ApiResponse rows are the immutable records that event produced. Together they
# are an append-only log — nothing here is ever updated in place, and everything
# downstream (computed statements, reconcile results) is derived from it and can
# be rebuilt from it without re-fetching.

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from db import Base


def _utcnow():
    # Naive UTC (tzinfo stripped) so values round-trip consistently through
    # SQLite, which has no native timezone-aware datetime type.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    ticker = Column(String, unique=True, nullable=False, index=True)
    company_name = Column(String)
    first_seen_at = Column(DateTime, default=_utcnow)

    fetches = relationship("Fetch", back_populates="company")


class Fetch(Base):
    __tablename__ = "fetches"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    source = Column(String, nullable=False, default="fmp")
    fetched_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    # "complete" once every expected statement landed; only complete fetches are
    # served from cache, so a half-failed fetch can never masquerade as a good one.
    status = Column(String, nullable=False, default="pending")

    company = relationship("Company", back_populates="fetches")
    api_responses = relationship("ApiResponse", back_populates="fetch")
    check_results = relationship("CheckResult", back_populates="fetch")
    restatements = relationship("Restatement", back_populates="fetch",
                                foreign_keys="Restatement.fetch_id")


class ApiResponse(Base):
    __tablename__ = "api_responses"

    id = Column(Integer, primary_key=True)
    fetch_id = Column(Integer, ForeignKey("fetches.id"), nullable=False, index=True)
    statement_type = Column(String, nullable=False)  # income_statement | cash_flow | balance_sheet | profile | ...
    fiscal_date = Column(String, nullable=True)      # e.g. "2024-09-28"; null for profile/rate pulls
    # Position this record held in FMP's response array. Stored, not inferred:
    # main.py treats position 0 as the most recent year, so order is load-bearing.
    year_position = Column(Integer, nullable=False, default=0)
    response_json = Column(Text, nullable=False)     # exactly what FMP returned, unmodified

    fetch = relationship("Fetch", back_populates="api_responses")


class CheckResult(Base):
    """One reconcile assertion: "our computed X for this year equals FMP's reported X."

    Derived data — every row can be rebuilt from the raw log by re-running the
    engines, so this table is a cache of an answer, not a source of truth. It hangs
    off fetch_id rather than off a company so a check is always traceable to the
    exact fetch whose numbers it validated.

    ours/reported/diff are stored as numbers, not formatted strings, so the table
    can actually be queried: "biggest mismatches", "which line items drift most",
    "did this company start failing after some date".
    """

    __tablename__ = "check_results"

    id = Column(Integer, primary_key=True)
    fetch_id = Column(Integer, ForeignKey("fetches.id"), nullable=False, index=True)

    statement_type = Column(String, nullable=False, index=True)  # income_statement | balance_sheet | cash_flow
    fiscal_date = Column(String, nullable=True, index=True)      # e.g. "2024-09-28"
    line_item = Column(String, nullable=False)                   # net_income, check_balance, ...

    # MATCH | MISMATCH | NO_REPORTED_VALUE
    status = Column(String, nullable=False, index=True)

    ours = Column(Float, nullable=True)
    reported = Column(Float, nullable=True)
    diff = Column(Float, nullable=True)  # ours - reported; null when there is nothing to compare

    checked_at = Column(DateTime, nullable=False, default=_utcnow)

    fetch = relationship("Fetch", back_populates="check_results")


class Restatement(Base):
    """One historical figure that FMP reported differently than it did last time.

    This is the second quality question, and it is not the one `check_results`
    answers. CheckResult asks "is our math right?" by comparing our computed
    subtotal against FMP's reported total *within a single fetch*. This table
    asks "did their data change?" by comparing one raw FMP field *across two
    fetches* of the same company.

    Deliberately compares the raw stored response, before any mapping is applied
    — the question is what FMP said, not what we made of it. That is only
    answerable because api_responses is append-only and nothing is overwritten.

    Companies restate prior periods, and data providers quietly fix bugs; either
    shows up here as a row.
    """

    __tablename__ = "restatements"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    # The fetch that introduced the change, and the one it was compared against.
    fetch_id = Column(Integer, ForeignKey("fetches.id"), nullable=False, index=True)
    prior_fetch_id = Column(Integer, ForeignKey("fetches.id"), nullable=False)

    statement_type = Column(String, nullable=False, index=True)
    fiscal_date = Column(String, nullable=True, index=True)
    field = Column(String, nullable=False)  # FMP's raw field name, not a model line

    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    delta = Column(Float, nullable=True)  # new - old

    detected_at = Column(DateTime, nullable=False, default=_utcnow)

    # Triage state. Without this the table only grows, and a finding from this
    # morning is indistinguishable from one already looked at six months ago —
    # which is how a real restatement ends up sitting undiscovered.
    #
    # The one column here that is deliberately mutable: every other table in this
    # schema is append-only, but "have we looked at this yet" is a fact about us,
    # not about what FMP reported, so updating it destroys no evidence.
    reviewed = Column(Integer, nullable=False, default=0, index=True)
    reviewed_at = Column(DateTime, nullable=True)

    company = relationship("Company")
    fetch = relationship("Fetch", foreign_keys=[fetch_id], back_populates="restatements")


class PipelineRun(Base):
    """One execution of a scheduled job.

    Without this a scheduled job is a script that prints into the void. This is
    what makes the pipeline observable: every run leaves a record of what it
    attempted, what it managed, and what it found.
    """

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True)
    job_name = Column(String, nullable=False, index=True)  # e.g. "refresh_stale"

    started_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    # running | success | partial | failed
    status = Column(String, nullable=False, default="running", index=True)

    tickers_attempted = Column(Integer, nullable=False, default=0)
    tickers_succeeded = Column(Integer, nullable=False, default=0)
    tickers_failed = Column(Integer, nullable=False, default=0)
    checks_written = Column(Integer, nullable=False, default=0)
    restatements_found = Column(Integer, nullable=False, default=0)

    notes = Column(Text, nullable=True)  # failure detail, or a one-line summary
