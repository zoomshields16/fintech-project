# Quick sanity check: insert one fake Company + RawPull row so you can see
# writes actually land in app.db before wiring this into the real FMP fetch.
# Run: python db_smoke_test.py

from db import SessionLocal
from models import Company, RawPull

session = SessionLocal()

company = session.query(Company).filter_by(ticker="TEST").first()
if not company:
    company = Company(ticker="TEST", company_name="Test Company Inc.")
    session.add(company)
    session.commit()
    session.refresh(company)

pull = RawPull(
    company_id=company.id,
    statement_type="income_statement",
    fiscal_date="2024-09-28",
    raw_json='{"revenue": 12345, "netIncome": 678}',
)
session.add(pull)
session.commit()

print(f"Inserted company id={company.id} ticker={company.ticker}")
print(f"Inserted raw_pull id={pull.id} for {pull.statement_type} {pull.fiscal_date}")

session.close()
