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
