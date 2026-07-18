# Run this once (or any time you add/change a model) to create app.db
# with the current table definitions: python init_db.py

from sqlalchemy import text

from db import Base, engine
import models  # noqa: F401  (import registers the model classes with Base)

# Per-company pull counts, as a view rather than columns on `companies`.
#
# Every fetch already writes a row to `fetches`, so the count is derivable.
# Storing it on `companies` would be a second copy of the same truth — one that
# silently goes stale if a fetch fails partway, or a row is deleted. A view is
# computed on read, so it cannot drift.
COMPANY_PULL_COUNTS_VIEW = """
CREATE VIEW IF NOT EXISTS company_pull_counts AS
SELECT
    c.id                AS company_id,
    c.ticker            AS ticker,
    c.company_name      AS company_name,
    COUNT(f.id)         AS total_pulls,
    SUM(CASE WHEN f.status = 'complete' THEN 1 ELSE 0 END) AS complete_pulls,
    MIN(f.fetched_at)   AS first_pulled_at,
    MAX(f.fetched_at)   AS last_pulled_at
FROM companies c
LEFT JOIN fetches f ON f.company_id = c.id
GROUP BY c.id, c.ticker, c.company_name
"""


def create_views(connection):
    """Create derived-data views. Safe to re-run — each uses IF NOT EXISTS."""
    connection.execute(text(COMPANY_PULL_COUNTS_VIEW))


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        create_views(connection)
    print("Tables created:", list(Base.metadata.tables.keys()))
    print("Views created: ['company_pull_counts']")
