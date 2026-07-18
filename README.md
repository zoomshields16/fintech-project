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
5. **Explain** — each historical year carries an equity roll-forward that
   accounts for the change in total equity (net income, other comprehensive
   income, dividends, buybacks, stock compensation, other financing). Whatever
   those terms do not explain is reported as an explicit `residual` rather than
   being absorbed into another line — an unexplained movement is a finding, not
   something to plug.
6. **Value** — historical statements feed a driver-based projection engine,
   unlevered free cash flow (NOPAT + D&A − CapEx − ΔNWC), and a DCF with WACC
   computation and sensitivity tables.

**All model computation lives in Python.** The frontend collects inputs and
renders results; it never computes a model line. That keeps the logic testable
and reachable by the validation pipeline.

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
    (`balance_sheet.compute_equity_rollforward` explains each year's equity change)
  - `checks.py`, `check_runner.py`, `backfill_checks.py` — validation pipeline
  - `projection_engine.py`, `dcf_engine.py` — forecasting and valuation
    (`dcf_engine.compute_ufcf` derives unlevered free cash flow)

### Database

`companies` → `fetches` → `api_responses`, plus `check_results`. One view,
`company_pull_counts`, reports per-company fetch counts; it is a view rather
than columns on `companies` so the counts are computed on read and cannot drift.

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
- [x] User-adjustable model drivers (growth, margins, CapEx, and optional
      R&D / D&A / tax / SBC / buyback overrides)
- [x] All model computation in Python — no engine logic in the frontend
- [x] Historical equity roll-forward with an explicit unexplained residual
- [ ] Equity roll-forward shown on the frontend (data is in the API, no UI yet)
- [ ] Check results shown on the frontend
- [ ] Output pages (Summary, DuPont, Drivers)
- [ ] Screener across many tickers
- [ ] Deploy to Railway (needs Postgres + prod table setup)
- [ ] Custom domain

## Deploy checklist (Railway / Render — known issues to fix at deploy time)

The app runs locally as-is, but deploying to a public URL needs these six
fixes. None are done yet — do them as one bundle when deploying:

1. **SQLite → Postgres.** Hosts give the app an ephemeral disk, so `app.db`
   is wiped on every redeploy. Provision the host's Postgres and set the
   `DATABASE_URL` environment variable (`db.py` already reads it). Add the
   Postgres driver (`psycopg2-binary`) to `requirements.txt`.
2. **Create tables on startup.** `init_db.py` is a manual script and never
   runs on a server — call `Base.metadata.create_all()` on app startup so a
   fresh database gets its tables.
3. **CORS + API URL.** `main.py` only allows requests from
   `localhost:5500` — add the deployed frontend's domain to `allow_origins`.
   Likewise the frontend JavaScript points at `127.0.0.1:8000` — point it at
   the deployed backend URL.
4. **FMP API key.** Set `FMP_API_KEY` as an environment variable in the
   host's dashboard. It is never committed to the repo.
5. **Concurrent-fetch race.** Two simultaneous requests for the same brand-new
   ticker can both try to insert the company row in `save_fetch`
   (read-then-insert race). Rare at low traffic, but fix before real users.
6. **`/api/run-dcf` trusts a client-supplied `ufcf` array.** UFCF is computed in
   Python now (`dcf_engine.compute_ufcf`), but the frontend still stores the
   result in `localStorage` and posts it back when running the DCF. The risk is
   staleness, not tampering: after a redeploy that changes the projection
   engine, a browser holding an old `dcf_input` will mix stale cash flows with a
   freshly computed WACC and return a wrong answer with no error. Fix by having
   the endpoint accept `drivers` and recompute the projection and UFCF
   server-side, so the DCF depends on nothing the client did.

## Tech Stack

- Backend: Python, FastAPI, SQLAlchemy, SQLite
- Frontend: HTML, CSS, JavaScript
- Data: Financial Modeling Prep API
- Hosting (planned): Railway