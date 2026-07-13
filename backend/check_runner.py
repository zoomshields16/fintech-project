# Runs the reconcile checks across every year of a pull batch and stores the results.
#
# This is the validation stage of the pipeline: for each fiscal year FMP gave us, we
# recompute the subtotals ourselves and assert they equal what FMP reported. Failures
# are recorded, not raised — a mismatch is a finding about the data, not a crash.
#
# Runs once per fetch (at ingest), so a cache hit costs nothing.

from db import SessionLocal
from models import CheckResult

from income_statement import pull_detail_accounts, compute_formula_lines, reconcile_rows
from cash_flow import pull_cf_accounts, compute_cf_formula_lines, reconcile_cf_rows
from balance_sheet import pull_bs_accounts, compute_bs_formula_lines, reconcile_bs_rows

# statement key -> (pull fn, compute fn, structured reconcile fn)
ENGINES = {
    "income_statement": (pull_detail_accounts, compute_formula_lines, reconcile_rows),
    "cash_flow": (pull_cf_accounts, compute_cf_formula_lines, reconcile_cf_rows),
    "balance_sheet": (pull_bs_accounts, compute_bs_formula_lines, reconcile_bs_rows),
}


def run_checks_for_batch(batch_id, data, ticker=None):
    """Validate every year of every statement in `data`. Returns rows written."""
    session = SessionLocal()
    written = 0
    try:
        for statement_type, (pull, compute, reconcile) in ENGINES.items():
            records = data.get(statement_type) or []
            for record in records:
                try:
                    # Full history + ticker: synonym resolution spans all years,
                    # and reclass adjustments are looked up per ticker.
                    rows = reconcile(record, compute(pull(record, records, ticker)), records)
                except Exception as e:
                    # A year we cannot even compute is itself a data-quality finding.
                    # Record it and keep going rather than failing the whole batch.
                    session.add(CheckResult(
                        batch_id=batch_id,
                        statement_type=statement_type,
                        fiscal_date=record.get("date"),
                        line_item="__engine_error__",
                        status="MISMATCH",
                        ours=None,
                        reported=None,
                        diff=None,
                    ))
                    written += 1
                    print(f"[checks] {statement_type} {record.get('date')}: engine error: {e}")
                    continue

                for r in rows:
                    session.add(CheckResult(
                        batch_id=batch_id,
                        statement_type=statement_type,
                        fiscal_date=record.get("date"),
                        line_item=r["line_item"],
                        status=r["status"],
                        ours=r["ours"],
                        reported=r["reported"],
                        diff=r["diff"],
                    ))
                    written += 1

        session.commit()
        return written
    finally:
        session.close()
