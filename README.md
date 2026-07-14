# fintech-project

A web-based stock valuation tool. Enter a ticker and the app pulls ten years
of real financial statements, rebuilds the three statements line by line,
projects them forward, and runs a discounted cash flow (DCF) model to estimate
fair value.

## How it works

```
FMP API  →  SQLite log  →  mapping engine  →  statement engines  →  projections  →  DCF
             (cache +        (rules from         (IS / BS / CF)
              raw log +       Excel model)
              checks)
```

1. **Fetch** — financial data comes from the
   [Financial Modeling Prep](https://site.financialmodelingprep.com/) (FMP) API.
2. **Store** — every response is saved verbatim to a local SQLite log
   (`fetches` → `api_responses`). Repeat searches within 24 hours are served
   from the database with zero API calls, and any past fetch can be recomputed
   later without touching the network.
3. **Map** — FMP's raw fields are messy: the same concept appears under
   different names for different companies, and some values are duplicated
   across fields. A data-driven mapping engine (`mapping_engine.py` +
   `mappings.json`, exported from our Excel reference model) resolves the right
   field per company using a priority/synonym system, plus per-company reclass
   rules for outliers.
4. **Validate** — on every fetch, the app re-sums its mapped line items and
   compares them against FMP's own reported totals. Every comparison is graded
   (MATCH / MISMATCH) and stored in `check_results`, so data-quality issues are
   caught and logged instead of silently corrupting valuations.
5. **Value** — historical statements feed a driver-based projection engine and
   a DCF with WACC computation and sensitivity tables.

## Architecture

- **Frontend** — plain HTML/CSS/JavaScript pages (`frontend/`): ticker input,
  three-statement model view, DCF output.
- **Backend** — Python + FastAPI (`backend/`):
  - `main.py` — API endpoints (`POST /api/run-model`, projections, DCF)
  - `fmp_test.py` — FMP fetch layer
  - `data_source.py` — cache-first data access
  - `db.py`, `models.py`, `db_write.py` — SQLite/SQLAlchemy persistence
  - `mapping_engine.py`, `mappings.json`, `export_mappings.py` — field mapping
  - `income_statement.py`, `balance_sheet.py`, `cash_flow.py` — statement engines
  - `checks.py`, `check_runner.py`, `backfill_checks.py` — validation pipeline
  - `projection_engine.py`, `dcf_engine.py` — forecasting and valuation

## Running locally

```bash
# 1. Install dependencies
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Add your FMP API key
cp backend/.env.example backend/.env   # then edit backend/.env

# 3. Create the database tables (first run only)
cd backend
python init_db.py

# 4. Start the API
uvicorn main:app --reload
```

Then open the frontend (e.g. with VS Code Live Server on port 5500) and search
a ticker.

To re-run the validation checks over everything already stored (no API calls):

```bash
python backfill_checks.py --all
```

## Status

- [x] End-to-end: ticker in → real statements, projections, and DCF out
- [x] SQLite caching + append-only raw fetch log
- [x] Data-driven mapping engine exported from the Excel reference model
- [x] Automated reconcile checks stored per fetch (`check_results`)
- [ ] Check results shown on the frontend
- [ ] User-adjustable model inputs
- [ ] Output pages (Summary, DuPont, Drivers)
- [ ] Screener across many tickers
- [ ] Deploy to Railway (needs Postgres + prod table setup)
- [ ] Custom domain

## Tech Stack

- Backend: Python, FastAPI, SQLAlchemy, SQLite
- Frontend: HTML, CSS, JavaScript
- Data: Financial Modeling Prep API
- Hosting (planned): Railway