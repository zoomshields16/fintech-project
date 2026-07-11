# SQLAlchemy models.
#
# Shape: companies -> pull_batches -> raw_pulls
#
# A PullBatch is one fetch event: "we went to FMP for AAPL at this instant."
# RawPull rows are the immutable records that event produced. Together they are
# an append-only log — nothing here is ever updated in place, and everything
# downstream (computed statements, reconcile results) is derived from it and can
# be rebuilt from it without re-fetching.

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
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

    batches = relationship("PullBatch", back_populates="company")


class PullBatch(Base):
    __tablename__ = "pull_batches"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    source = Column(String, nullable=False, default="fmp")
    fetched_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    # "complete" once every expected statement landed; only complete batches are
    # served from cache, so a half-failed fetch can never masquerade as a good one.
    status = Column(String, nullable=False, default="pending")

    company = relationship("Company", back_populates="batches")
    raw_pulls = relationship("RawPull", back_populates="batch")


class RawPull(Base):
    __tablename__ = "raw_pulls"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("pull_batches.id"), nullable=False, index=True)
    statement_type = Column(String, nullable=False)  # income_statement | cash_flow | balance_sheet | profile | ...
    fiscal_date = Column(String, nullable=True)      # e.g. "2024-09-28"; null for profile/rate pulls
    # Position this record held in FMP's response array. Stored, not inferred:
    # main.py treats index 0 as the most recent year, so order is load-bearing.
    seq = Column(Integer, nullable=False, default=0)
    raw_json = Column(Text, nullable=False)          # exactly what FMP returned, unmodified

    batch = relationship("PullBatch", back_populates="raw_pulls")
