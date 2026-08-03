# Copy the local SQLite database into Postgres, row for row.
#
# This is a MOVE, not a re-seed. Re-seeding would mean re-fetching every company
# from FMP, and that would destroy the thing the database exists to hold: the
# fetch history is the evidence behind every restatement finding. A restatement
# is only detectable because two fetches of the same company, taken at different
# times, disagree — re-fetching today would collapse that history into a single
# fresh pull and every prior finding would become unreproducible. It would also
# burn ~100 FMP calls to end up with strictly less information.
#
# Usage, from backend/:
#   DATABASE_URL="postgresql+psycopg2://user@host:5432/dbname" \
#       ../venv/bin/python migrate_to_postgres.py
#
# Add --force to overwrite a target that already holds rows.
#
# Two details this script exists to get right, both of which bite silently:
#
#   1. PRIMARY KEYS ARE COPIED AS-IS. Every table here is linked by integer id
#      (a check_result points at a fetch, a restatement at two of them), so
#      letting Postgres assign fresh ids would silently rewire those references
#      to the wrong rows. The ids are carried over unchanged instead.
#
#   2. SEQUENCES MUST THEN BE RESET. Inserting explicit ids does not advance
#      Postgres's id counter, so it still points at 1 while the table already
#      holds id 107. The very next insert would collide on the primary key.
#      Nothing warns you about this until the app writes its first row, which is
#      long after the migration "succeeded". _reset_sequences fixes it.
#
# Datetime columns need no special handling: SQLite stores them as text, but
# SQLAlchemy parses them back into datetime objects on read because the model
# declares the column as DateTime, so the values arrive at Postgres already
# typed. That only holds because the read goes through the model metadata, which
# is why this reads through SQLAlchemy rather than raw sqlite3.

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select, text

from db import Base, DB_PATH
import models  # noqa: F401  (registers the model classes with Base)

BATCH = 500


def _engines(source_path, target_url):
    source = create_engine(f"sqlite:///{source_path}",
                           connect_args={"check_same_thread": False})
    target = create_engine(target_url)
    return source, target


def _counts(engine, tables):
    out = {}
    with engine.connect() as conn:
        for table in tables:
            out[table.name] = conn.execute(
                select(func.count()).select_from(table)).scalar()
    return out


def _reset_sequences(target, tables):
    """Point each table's id counter past the largest id just inserted.

    Only applies to Postgres; SQLite has no sequences to reset.
    """
    if target.dialect.name != "postgresql":
        return
    with target.begin() as conn:
        for table in tables:
            if "id" not in table.c:
                continue
            conn.execute(text(
                "SELECT setval("
                "  pg_get_serial_sequence(:tbl, 'id'),"
                "  COALESCE((SELECT MAX(id) FROM " + table.name + "), 1)"
                ")"
            ), {"tbl": table.name})


def migrate(source_path, target_url, force=False):
    source, target = _engines(source_path, target_url)

    # sorted_tables is dependency-ordered, so parents land before the rows that
    # reference them and no foreign key is ever briefly dangling.
    tables = list(Base.metadata.sorted_tables)

    Base.metadata.create_all(bind=target)

    existing = _counts(target, tables)
    occupied = {name: n for name, n in existing.items() if n}
    if occupied and not force:
        print("Target database is not empty:")
        for name, n in sorted(occupied.items()):
            print(f"  {name:20} {n:>8,} rows")
        print("\nRefusing to write into it. Re-run with --force to replace "
              "these rows, or point DATABASE_URL at an empty database.")
        return False

    if occupied:
        # Reverse order: children first, so nothing is deleted while a row still
        # references it.
        with target.begin() as conn:
            for table in reversed(tables):
                conn.execute(table.delete())

    source_counts = _counts(source, tables)
    print(f"Source: {source_path}")
    print(f"Target: {target.url.render_as_string(hide_password=True)}\n")

    for table in tables:
        total = source_counts[table.name]
        if not total:
            print(f"  {table.name:20} empty, skipped")
            continue

        moved = 0
        with source.connect() as src_conn:
            # stream_results keeps api_responses (one full FMP payload per row)
            # from being pulled into memory all at once.
            result = src_conn.execution_options(stream_results=True).execute(
                select(table))
            while True:
                rows = [dict(r) for r in result.mappings().fetchmany(BATCH)]
                if not rows:
                    break
                with target.begin() as dst_conn:
                    dst_conn.execute(insert(table), rows)
                moved += len(rows)

        print(f"  {table.name:20} {moved:>8,} rows")

    _reset_sequences(target, tables)

    # Verify by re-counting the target rather than trusting the loop above.
    final = _counts(target, tables)
    mismatched = {name: (source_counts[name], final[name])
                  for name in final if source_counts[name] != final[name]}
    if mismatched:
        print("\nROW COUNTS DO NOT MATCH — migration is incomplete:")
        for name, (want, got) in sorted(mismatched.items()):
            print(f"  {name:20} expected {want:,}, found {got:,}")
        return False

    print(f"\nVerified: {sum(final.values()):,} rows, every table matching.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DB_PATH),
                        help="path to the SQLite file (default: backend/app.db)")
    parser.add_argument("--target", default=None,
                        help="target URL (default: the DATABASE_URL env var)")
    parser.add_argument("--force", action="store_true",
                        help="replace rows in a target that is not empty")
    args = parser.parse_args()

    target_url = args.target or __import__("os").getenv("DATABASE_URL")
    if not target_url or target_url.startswith("sqlite"):
        sys.exit("Set DATABASE_URL (or pass --target) to a Postgres URL. "
                 "Migrating into SQLite is not what this script is for.")
    if not Path(args.source).exists():
        sys.exit(f"No SQLite database at {args.source}")

    sys.exit(0 if migrate(args.source, target_url, args.force) else 1)
