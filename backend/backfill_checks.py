# Re-runs the reconcile checks over pull batches we already have.
#
# Checks are derived data: they are pure recomputation over the raw log, so they can
# always be rebuilt without touching FMP. That is what makes this safe to re-run after
# an engine or synonym-map change — rerun it and every historical pull is re-validated
# against the new logic, using zero API calls.
#
#   python backfill_checks.py          # only batches with no checks yet
#   python backfill_checks.py --all    # wipe and re-validate every complete batch

import sys

from db import SessionLocal
from models import Company, PullBatch, CheckResult
from db_write import load_batch
from check_runner import run_checks_for_batch


def main(rebuild_all=False):
    session = SessionLocal()
    try:
        batches = (
            session.query(PullBatch, Company.ticker)
            .join(Company, Company.id == PullBatch.company_id)
            .filter(PullBatch.status == "complete")
            .order_by(PullBatch.id)
            .all()
        )

        if rebuild_all:
            deleted = session.query(CheckResult).delete()
            session.commit()
            print(f"cleared {deleted} existing check rows")

        targets = []
        for batch, ticker in batches:
            already = session.query(CheckResult).filter_by(batch_id=batch.id).count()
            if already and not rebuild_all:
                print(f"skip  {ticker:<6} batch {batch.id}  ({already} checks already)")
                continue
            targets.append((batch.id, ticker))
    finally:
        session.close()

    for batch_id, ticker in targets:
        data = load_batch(batch_id)  # from our own log — no FMP call
        written = run_checks_for_batch(batch_id, data, ticker)
        print(f"check {ticker:<6} batch {batch_id}  -> {written} rows")

    print(f"\ndone: {len(targets)} batches validated, 0 API calls")


if __name__ == "__main__":
    main(rebuild_all="--all" in sys.argv)
