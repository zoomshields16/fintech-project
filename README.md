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
  - `fmp_client.py` — FMP fetch layer
  - `data_source.py` — cache-first data access
  - `db.py`, `models.py`, `db_write.py` — SQLite/SQLAlchemy persistence
  - `mapping_engine.py`, `mappings.json`, `export_mappings.py`,
    `apply_overrides.py` — field mapping
  - `income_statement.py`, `balance_sheet.py`, `cash_flow.py` — statement engines
    (`balance_sheet.compute_equity_rollforward` explains each year's equity change)
  - `checks.py`, `check_runner.py`, `backfill_checks.py` — validation pipeline
  - `projection_engine.py`, `dcf_engine.py` — forecasting and valuation
    (`dcf_engine.compute_ufcf` derives unlevered free cash flow)

  - `refresh_stale.py`, `restatement_detector.py` — scheduled refresh and
    upstream-change detection
  - `tests/` — unit tests for the engine math and mapping semantics (pytest)

### Loading a new model workbook

The Excel workbook is the source of truth for the mappings and `mappings.json` is
a build artifact of it. When a new workbook lands, run both steps — never one:

```bash
python export_mappings.py "../reference/model 56.xlsm"   # rebuild from the workbook
python apply_overrides.py                                # reapply our decisions
python backfill_checks.py --all                          # re-grade, 0 API calls
```

**`apply_overrides.py` is not optional.** The export overwrites `mappings.json`
wholesale, so anything edited directly into the JSON is destroyed by the next
export. That nearly happened: three fixes worth ~4.5 points of pass rate lived
only in the JSON, and model 56's export would have silently reverted them. The
overrides now live in code — versioned, commented with the reasoning, and
reapplied identically every time. The script is idempotent and prints what it
changed, so a second run reporting `0 override(s) applied` is the expected result.

Where we knowingly diverge from the workbook is documented inline in that file.
The substantive one: our net income is `pretax - tax`, which is consolidated and
**pre**-NCI, while FMP's `netIncome` is **post**-NCI, so the check is pointed at
`netIncomeFromContinuingOperations` instead. That one mapping change closes the
gap for every company at once, which is why the workbook's per-ticker reclasses
into `Other Income (Expense)` are dropped — they patch the same gap a second
time, and because Other Income sits *above* pretax while NCI sits *below* the tax
line, they fix `net_income` while breaking `pretax_income` by the same amount.

### Database

`companies` → `fetches` → `api_responses`, plus `check_results`,
`restatements`, and `pipeline_runs`. One view, `company_pull_counts`, reports
per-company fetch counts; it is a view rather than columns on `companies` so the
counts are computed on read and cannot drift.

### Two quality layers

The pipeline answers two different questions, and neither can find what the
other finds:

| Table | Question | Compares |
|---|---|---|
| `check_results` | Is *our* math right? | our computed subtotal vs FMP's reported total, **within one fetch** |
| `restatements` | Did *their* data change? | one raw FMP field, **across two fetches** of the same company |

Restatement detection reads the raw stored response, before any mapping is
applied — the question is what FMP said, not what we made of it. It is only
answerable because `api_responses` is append-only: a pipeline that overwrote each
fetch would have destroyed the evidence.

### Scheduled refresh

`refresh_stale.py` finds companies whose data has gone stale, re-fetches them,
re-runs the checks, and compares each new fetch against the previous one. Every
execution writes a `pipeline_runs` row recording what it attempted, what
succeeded, and what it found.

```bash
python refresh_stale.py --dry-run     # show what would be refreshed
python refresh_stale.py --days 1      # tighter staleness window
python refresh_stale.py --limit 5     # cap tickers per run (API budget)
```

Tickers that never return usable data are retried a few times and then reported
rather than retried forever, so a typo'd symbol cannot quietly drain the API
budget. Wire to a scheduler at deploy time, e.g. nightly:

```
0 2 * * *  cd /path/to/backend && /path/to/venv/bin/python refresh_stale.py
```

### Surfacing what it finds

A finding that sits in a table nobody opens is not a finding. Three things make
the pipeline's output visible:

- **`backend/logs/pipeline.log`** — every run appends here, with restatements
  called out at the end of the run rather than buried mid-log.
- **`GET /api/pipeline-status`** — coverage, reconcile pass rates, the worst
  line items, recent job runs, and the unreviewed restatement queue, all in one
  response. Rates are measured against the *latest* complete fetch per company,
  so old fetches taken before a mapping fix don't drag down a number meant to
  describe the engine as it stands today.
- **`frontend/status.html`** — that endpoint rendered, with a button to mark
  findings reviewed.

The `reviewed` flag on `restatements` is what turns the table into a queue you
can work down instead of a pile that only grows. It is the one deliberately
mutable column in an otherwise append-only schema — "have we looked at this yet"
is a fact about us, not about what FMP reported, so updating it destroys no
evidence.

`app.html` also shows a banner when the loaded company has restated figures, so
the notice reaches whoever is valuing that stock without them going looking.

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

To run the unit tests (from `backend/`):

```bash
python -m pytest tests/
```

The reconcile pipeline grades our math against FMP's reported totals; the unit
tests grade it against answers computed by hand. They cover the mapping engine's
resolution semantics (priority, ties-are-additive, whole-history HasData), the
UFCF/WACC/DCF math, the equity roll-forward, and — via `test_shipped_mappings.py`
— the content of `mappings.json` itself, so an export run without
`apply_overrides.py` now fails a test instead of silently losing pass rate.

## Status

- [x] End-to-end: ticker in → real statements, projections, and DCF out
- [x] SQLite caching + append-only raw fetch log
- [x] Data-driven mapping engine exported from the Excel reference model
- [x] Automated reconcile checks stored per fetch (`check_results`)
- [x] User-adjustable model drivers (growth, margins, CapEx, and optional
      R&D / D&A / tax / SBC / buyback overrides)
- [x] All model computation in Python — no engine logic in the frontend
- [x] Historical equity roll-forward with an explicit unexplained residual
- [x] Restatement detection — flags figures FMP reports differently than before
- [x] Scheduled refresh job with a `pipeline_runs` audit log
- [x] Unit tests on the engines and the shipped mapping spec (`backend/tests/`)
- [ ] Wire the refresh job to a real scheduler (needs deploy — see checklist)
- [ ] Reject tickers outside the Nasdaq-100 (the supported universe) instead of
      attempting a fetch
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