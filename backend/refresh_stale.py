# Scheduled refresh job.
#
# Finds companies whose stored data has gone stale, re-fetches them, re-runs the
# reconcile checks, and compares each new fetch against the previous one to catch
# figures FMP has silently changed.
#
# This is what makes the project a pipeline rather than a request/response app:
# it does work nobody asked for, on a timer, and leaves a record of what it did.
#
#   python refresh_stale.py                  # refresh anything older than 7 days
#   python refresh_stale.py --days 1         # tighter staleness window
#   python refresh_stale.py --limit 5        # cap the number of tickers (API budget)
#   python refresh_stale.py --dry-run        # show what would be refreshed
#
# Wire to a scheduler at deploy time, e.g. a nightly cron entry:
#   0 2 * * *  cd /path/to/backend && /path/to/venv/bin/python refresh_stale.py

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sqlalchemy import func

from db import SessionLocal
from models import Company, Fetch, CheckResult, PipelineRun
from data_source import get_financials_cached
from restatement_detector import detect_restatements
from universe import universe_symbols

JOB_NAME = "refresh_stale"
DEFAULT_STALE_DAYS = 7

# A scheduled job prints into a console nobody is watching. Everything it says is
# also appended here so a restatement found at 2am is still discoverable at 9am.
LOG_PATH = Path(__file__).resolve().parent / "logs" / "pipeline.log"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def log(message):
    """Print and append to the job log."""
    print(message)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as handle:
            handle.write(f"{_utcnow():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError as e:
        # Logging must never take the job down.
        print(f"[{JOB_NAME}] warning: could not write log — {e}")


# A company that has never returned usable data is retried a few times in case the
# failure was transient, then left alone. Without this, a typo'd ticker that FMP
# does not cover would be re-fetched on every run forever.
MAX_ATTEMPTS_WITHOUT_SUCCESS = 3


def find_stale_tickers(session, stale_days, limit=None):
    """Companies due for a refresh: a complete fetch older than `stale_days`, or
    no complete fetch yet but still within the retry budget.

    Oldest first, so a capped run always refreshes the most neglected companies.
    """
    cutoff = _utcnow() - timedelta(days=stale_days)

    newest = (
        session.query(
            Fetch.company_id.label("company_id"),
            func.max(Fetch.fetched_at).label("last_fetched"),
        )
        .filter(Fetch.status == "complete")
        .group_by(Fetch.company_id)
        .subquery()
    )

    attempts = (
        session.query(
            Fetch.company_id.label("company_id"),
            func.count(Fetch.id).label("attempts"),
        )
        .group_by(Fetch.company_id)
        .subquery()
    )

    rows = (
        session.query(Company.ticker, newest.c.last_fetched,
                      func.coalesce(attempts.c.attempts, 0))
        .outerjoin(newest, newest.c.company_id == Company.id)
        .outerjoin(attempts, attempts.c.company_id == Company.id)
        .filter(
            (newest.c.last_fetched < cutoff)
            | ((newest.c.last_fetched == None)  # noqa: E711
               & (func.coalesce(attempts.c.attempts, 0) < MAX_ATTEMPTS_WITHOUT_SUCCESS))
        )
        .order_by(newest.c.last_fetched.asc().nullsfirst())
        .all()
    )
    rows = [(ticker, last) for ticker, last, _ in rows]
    return rows[:limit] if limit else rows


def find_unseeded_tickers(session, limit=None):
    """Universe members with no company row yet — never fetched even once.

    This is what turns the dataset from accidental (whatever users happened to
    search) into deliberate (the whole supported universe).
    """
    known = {ticker for (ticker,) in session.query(Company.ticker).all()}
    missing = [symbol for symbol in universe_symbols() if symbol not in known]
    return missing[:limit] if limit else missing


def find_abandoned_tickers(session):
    """Companies that have exhausted the retry budget without ever succeeding.

    Reported rather than retried — usually an invalid ticker or one FMP does not
    cover. Surfacing them keeps a bad ticker from silently consuming API budget.
    """
    complete = (
        session.query(Fetch.company_id)
        .filter(Fetch.status == "complete")
        .distinct()
        .subquery()
    )
    return (
        session.query(Company.ticker, func.count(Fetch.id))
        .join(Fetch, Fetch.company_id == Company.id)
        .filter(~Company.id.in_(session.query(complete.c.company_id)))
        .group_by(Company.ticker)
        .having(func.count(Fetch.id) >= MAX_ATTEMPTS_WITHOUT_SUCCESS)
        .all()
    )


def refresh_ticker(ticker):
    """Force a fresh pull for one ticker. Returns (checks_written, restatements_found).

    max_age_hours=0 bypasses the cache — a refresh job that served itself from the
    cache would do nothing at all. Checks run inside get_financials_cached; the
    restatement comparison is counted here off the rows it wrote.
    """
    before = _count_checks(ticker)
    get_financials_cached(ticker, max_age_hours=0)
    after = _count_checks(ticker)
    return after - before, detect_restatements(ticker)


def _count_checks(ticker):
    session = SessionLocal()
    try:
        return (session.query(CheckResult)
                .join(Fetch, Fetch.id == CheckResult.fetch_id)
                .join(Company, Company.id == Fetch.company_id)
                .filter(Company.ticker == ticker)
                .count())
    finally:
        session.close()


def main(stale_days=DEFAULT_STALE_DAYS, limit=None, dry_run=False, seed=False):
    session = SessionLocal()
    try:
        unseeded = find_unseeded_tickers(session, limit) if seed else []
        # Seeding consumes the limit first — a brand-new company has no data at
        # all, which is more urgent than refreshing one that is merely stale.
        remaining = None if limit is None else max(0, limit - len(unseeded))
        stale = find_stale_tickers(session, stale_days, remaining) if remaining != 0 else []
        abandoned = find_abandoned_tickers(session)
    finally:
        session.close()

    if abandoned:
        log(f"[{JOB_NAME}] skipping {len(abandoned)} ticker(s) with no usable data "
            f"after {MAX_ATTEMPTS_WITHOUT_SUCCESS}+ attempts "
            f"(likely invalid or uncovered by FMP):")
        for ticker, attempts in abandoned:
            log(f"  {ticker:<6} {attempts} failed attempt(s)")

    work = [(ticker, None, "seed") for ticker in unseeded] + \
           [(ticker, last, "refresh") for ticker, last in stale]

    if dry_run:
        log(f"[{JOB_NAME}] dry run — {len(unseeded)} to seed, {len(stale)} to refresh "
            f"(threshold {stale_days} day(s)):")
        for ticker, last, kind in work:
            detail = "NEW — never fetched" if kind == "seed" else f"last complete fetch: {last}"
            log(f"  {ticker:<6} [{kind}] {detail}")
        return

    if not work:
        log(f"[{JOB_NAME}] nothing to do "
            f"(no unseeded universe members, nothing older than {stale_days} days)")
        return

    session = SessionLocal()
    run = PipelineRun(job_name=JOB_NAME, started_at=_utcnow(), status="running",
                      tickers_attempted=len(work))
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    log(f"[{JOB_NAME}] run #{run_id} starting — "
        f"{len(unseeded)} to seed, {len(stale)} to refresh")

    succeeded = failed = checks_total = restatements_total = 0
    failures = []
    restated_tickers = []

    for ticker, last, kind in work:
        try:
            checks, restated = refresh_ticker(ticker)
            checks_total += checks
            restatements_total += restated
            succeeded += 1
            if restated:
                restated_tickers.append(f"{ticker} ({restated})")
                log(f"[{JOB_NAME}] {ticker:<6} {kind}ed, {checks} checks "
                    f"** {restated} RESTATED FIGURE(S) — REVIEW **")
            else:
                log(f"[{JOB_NAME}] {ticker:<6} {kind}ed, {checks} checks")
        except Exception as e:
            failed += 1
            failures.append(f"{ticker}: {e}")
            log(f"[{JOB_NAME}] {ticker:<6} FAILED — {e}")

    # A run that refreshed some tickers and lost others is neither success nor
    # failure; record it as partial so the distinction survives.
    status = "success" if not failed else ("failed" if not succeeded else "partial")

    session = SessionLocal()
    try:
        run = session.query(PipelineRun).filter_by(id=run_id).first()
        run.finished_at = _utcnow()
        run.status = status
        run.tickers_succeeded = succeeded
        run.tickers_failed = failed
        run.checks_written = checks_total
        run.restatements_found = restatements_total
        run.notes = "; ".join(failures) if failures else (
            f"restated: {', '.join(restated_tickers)}" if restated_tickers else "clean")
        session.commit()
    finally:
        session.close()

    log(f"[{JOB_NAME}] run #{run_id} {status}: {succeeded} ok, {failed} failed, "
        f"{checks_total} checks written, {restatements_total} restatement(s) found")

    # Restatements are the finding this job exists to surface. Repeat them at the
    # end so they are the last thing in the log rather than buried mid-run.
    if restated_tickers:
        log(f"[{JOB_NAME}] ** REVIEW NEEDED — FMP changed previously reported figures: "
            f"{', '.join(restated_tickers)} **")
        log(f"[{JOB_NAME}]    query: SELECT * FROM restatements ORDER BY detected_at DESC;")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed and refresh the supported ticker universe from FMP.")
    parser.add_argument("--days", type=int, default=DEFAULT_STALE_DAYS,
                        help=f"staleness threshold in days (default {DEFAULT_STALE_DAYS})")
    parser.add_argument("--limit", type=int, default=None,
                        help="maximum tickers to process in one run")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be processed without calling FMP")
    parser.add_argument("--seed", action="store_true",
                        help="also fetch universe members not yet in the database")
    args = parser.parse_args()
    main(stale_days=args.days, limit=args.limit, dry_run=args.dry_run, seed=args.seed)
