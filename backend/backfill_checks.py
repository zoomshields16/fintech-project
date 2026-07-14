# Re-runs the reconcile checks over fetches we already have.
#
# Checks are derived data: they are pure recomputation over the raw log, so they can
# always be rebuilt without touching FMP. That is what makes this safe to re-run after
# an engine or synonym-map change — rerun it and every historical fetch is re-validated
# against the new logic, using zero API calls.
#
#   python backfill_checks.py          # only fetches with no checks yet
#   python backfill_checks.py --all    # wipe and re-validate every complete fetch

import sys

from db import SessionLocal
from models import Company, Fetch, CheckResult
from db_write import load_fetch
from check_runner import run_checks_for_fetch


def main(rebuild_all=False):
    session = SessionLocal()
    try:
        fetches = (
            session.query(Fetch, Company.ticker)
            .join(Company, Company.id == Fetch.company_id)
            .filter(Fetch.status == "complete")
            .order_by(Fetch.id)
            .all()
        )

        if rebuild_all:
            deleted = session.query(CheckResult).delete()
            session.commit()
            print(f"cleared {deleted} existing check rows")

        targets = []
        for fetch, ticker in fetches:
            already = session.query(CheckResult).filter_by(fetch_id=fetch.id).count()
            if already and not rebuild_all:
                print(f"skip  {ticker:<6} fetch {fetch.id}  ({already} checks already)")
                continue
            targets.append((fetch.id, ticker))
    finally:
        session.close()

    for fetch_id, ticker in targets:
        data = load_fetch(fetch_id)  # from our own log — no FMP call
        written = run_checks_for_fetch(fetch_id, data, ticker)
        print(f"check {ticker:<6} fetch {fetch_id}  -> {written} rows")

    print(f"\ndone: {len(targets)} fetches validated, 0 API calls")


if __name__ == "__main__":
    main(rebuild_all="--all" in sys.argv)