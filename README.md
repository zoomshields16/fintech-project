# The Retail Analyst

[![tests](https://github.com/zoomshields16/fintech-project/actions/workflows/tests.yml/badge.svg)](https://github.com/zoomshields16/fintech-project/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Live at [theretailanalyst.com](https://theretailanalyst.com)** · [pipeline status
board](https://theretailanalyst.com/status.html) · [API](https://api.theretailanalyst.com/api/pipeline-status)

A stock valuation tool that rebuilds public companies' financial statements from
raw vendor data and **grades its own arithmetic against the filed totals before
valuing anything**. Enter a ticker: it pulls ten years of statements, remaps them
line by line, projects them forward, and runs a DCF.

The valuation is the easy half. The interesting half is that vendor financial
data is inconsistent between companies and silently revised over time, so the
pipeline treats every number it produces as a claim to be checked:

| | |
|---|---|
| **97.0%** | reconcile pass rate — our recomputed subtotals vs. the vendor's own reported totals |
| **12,538** | automated checks, re-graded on every fetch (12,158 match, 380 mismatch) |
| **101** | Nasdaq-100 companies, ~10 fiscal years each, refreshed nightly |
| **93.3%** | strict pass rate (exact match, no materiality threshold) |
| **76** | unit tests, run in CI on every push |

Every mismatch is stored with both values and the difference, so a data-quality
problem surfaces as a row in a queue rather than a wrong valuation. Several of
the remaining 380 are cases where **our number is right and the vendor's is
wrong** — see [How a reclass is directed](#how-a-reclass-is-directed).

<!-- TODO before calling the project done: add a screenshot of the status board.
     Capture https://theretailanalyst.com/status.html with the tiles in frame,
     resize to ~1400px, save as frontend/assets/screenshot-status.png (the
     .gitignore already allows screenshot-*.png), then restore the line below.
![Pipeline status board](frontend/assets/screenshot-status.png)
-->


## How it works

```
FMP API  →  raw log  →  mapping engine  →  statement engines  →  projections  →  DCF
            (cache +      (rules from        (IS / BS / CF)
             append-only   Excel model)
             + checks)
```

1. **Fetch** — financial data comes from the
   [Financial Modeling Prep](https://site.financialmodelingprep.com/) (FMP) API.
2. **Store** — every response is saved verbatim to an append-only log
   (`fetches` → `api_responses`) — Postgres in production, SQLite locally, same
   SQLAlchemy models either way. Repeat searches are served from the database
   with zero API calls, and any past fetch can be recomputed later without
   touching the network. Nothing is ever overwritten, which is what makes
   restatement detection possible at all.
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
  three-statement model view, DCF output, pipeline status board. `config.js` is
  the single place the API address lives — it picks localhost vs. production off
  `location.hostname`, so no page hardcodes a URL.
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
python export_mappings.py "../reference/model 80.xlsm"   # rebuild from the workbook
python apply_overrides.py                                # reapply our decisions
python backfill_checks.py --all                          # re-grade, 0 API calls
```

The exporter assumes nothing about where the two tables sit. Model 62 moved both
one column right and swapped their order, so each is located by its own header
labels and bounded by the other. Live per-ticker formula columns (`Active`, and
model 62's `Applies now?`) are never exported — the engine recomputes them from
real data, and freezing one company's shape would corrupt every other company.

**`apply_overrides.py` is not optional.** The export overwrites `mappings.json`
wholesale, so anything edited directly into the JSON is destroyed by the next
export. That nearly happened: three fixes worth ~4.5 points of pass rate lived
only in the JSON, and model 56's export would have silently reverted them. The
overrides now live in code — versioned, commented with the reasoning, and
reapplied identically every time. The script is idempotent and prints what it
changed, so a second run reporting `0 override(s) applied` is the expected result.

### How a reclass is directed

A reclass row is a transfer: the amount is subtracted from `from_line` and added
to `to_line`. The workbook says which it is by which of the two columns holds a
real model line — a gross-up names only the target and describes the source in
prose ("FMP unallocated non-current assets"); a removal names only the source and
describes the target in prose ("FMP over-listed NCL"). Prose matches no model line
and is dropped, and that asymmetry is what makes one add and the other subtract.

Note that a reclass can make a check *fail* while making the model more correct.
Carson's corrections are sourced to the audited 10-K, but the checks grade against
FMP, so where FMP is the party that is wrong the two disagree by design. MAR
fiscal 2017 is the clean example: our total liabilities now come to $20,217M,
which is the 10-K figure to the dollar, and the check reports a mismatch because
FMP carries $20,264M.

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

### What is not reconciled, and why

Free Cash Flow is computed and displayed but deliberately not graded. FMP derives
its `freeCashFlow` from `capitalExpenditure`, while our CapEx maps to
`investmentsInPropertyPlantAndEquipment` (FMP's own field is kept only as a
priority-2 backup), so the check fired whenever those two FMP fields disagreed and
measured the field choice rather than our math. MSTR shows why ours is the better
number: FMP's `capitalExpenditure` absorbs roughly $22B of bitcoin purchases where
ours is $13.5M of actual property and equipment. Nothing downstream wanted the
check either — the valuation runs on unlevered free cash flow from
`dcf_engine.compute_ufcf`, which never reads this line.

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
budget.

**In production this runs nightly on Railway** as a separate cron service, config
in [`railway.cron.json`](railway.cron.json):

```
0 7 * * *   python backend/refresh_stale.py --days 5 --limit 50
```

The two numbers are the interesting part. All 101 companies were seeded inside a
four-minute window, so they all fall due on the same night, and `find_stale_tickers`
orders oldest-first and truncates at `--limit` — which means a cap that is too low
lets the tail of the universe age well past its target. At `--limit 20` the backlog
took five nights to clear and the last companies reached **ten days** stale. Raising
the cap alone was not enough either: it clears the backlog in two nights, but the
second night's companies are still 7.4 days old. Tightening the staleness threshold
to five days is what actually holds the guarantee, because it starts the cycle a day
earlier — worst case **6.4 days**, against a requirement of one week.

Note that `stale_after_days` on the status board is deliberately a *different*
number (7). The job refreshes at 5 and the alarm trips at 7, so a healthy pipeline
keeps the stale count pinned at zero and any non-zero reading is a real signal
rather than normal lag.

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

Shipped:

- [x] End-to-end: ticker in → real statements, projections, and DCF out
- [x] Append-only raw fetch log with cache-first reads (Postgres / SQLite)
- [x] Data-driven mapping engine exported from the Excel reference model
- [x] Automated reconcile checks stored per fetch (`check_results`)
- [x] User-adjustable model drivers (growth, margins, CapEx, and optional
      R&D / D&A / tax / SBC / buyback overrides)
- [x] All model computation in Python — no engine logic in the frontend
- [x] Historical equity roll-forward with an explicit unexplained residual
- [x] Restatement detection — flags figures FMP reports differently than before
- [x] Scheduled refresh job with a `pipeline_runs` audit log
- [x] Unit tests on the engines and the shipped mapping spec, green in CI
- [x] **Deployed** — API on Railway, frontend on Cloudflare, custom domain, TLS
- [x] **Nightly refresh cron** with a staleness guarantee under one week
- [x] Ticker scope enforced at the input box against `GET /api/universe`

Next:

- [ ] Equity roll-forward shown on the frontend (data is in the API, no UI yet)
- [ ] Check results shown on the frontend
- [ ] Output pages (Summary, DuPont, Drivers)
- [ ] Screener across the universe, and a reverse DCF (solve for the growth rate
      the current price implies)

## Deployment

```
theretailanalyst.com          api.theretailanalyst.com         cron-refresh
  Cloudflare Worker    ──────▶   Railway (FastAPI)     ◀──────  Railway cron
  static frontend                       │                      0 7 * * * UTC
                                        ▼
                                 Railway Postgres
                                   (private)
```

Three things about this setup were deliberate:

- **Postgres stays private.** Reaching it from a laptop goes through
  `railway connect Postgres --tunnel-only` rather than switching on public
  access, which would assign a permanent internet-reachable host/port that is
  easy to leave enabled after a one-minute migration.
- **No environment-specific code.** `db.py` reads `DATABASE_URL`, CORS reads
  `ALLOWED_ORIGINS`, and `frontend/config.js` picks the API host off
  `location.hostname`. Local development is unchanged and no deploy needs a
  code edit. The API creates missing tables on startup via a lifespan hook, so
  an empty database bootstraps itself.
- **Only the write endpoint is authenticated.** `POST /api/restatements/review`
  requires `X-API-Key`; an unset key refuses writes with a 503 rather than
  failing open. The GETs are open **on purpose** — the status board is meant to
  be looked at — and `test_api_auth.py` asserts that, so locking them down has
  to be a deliberate act rather than a drift.

Migration to a fresh host is `backend/migrate_to_postgres.py`, which copies row
for row rather than re-seeding: re-fetching from the vendor would collapse the
fetch history that makes restatements detectable in the first place.

## Known limitations

Things I know are wrong and have chosen not to fix yet, rather than things I
haven't noticed:

1. **`/api/run-dcf` trusts a client-supplied `ufcf` array.** UFCF is computed in
   Python (`dcf_engine.compute_ufcf`), but the frontend caches the result in
   `localStorage` and posts it back. The exposure is staleness, not tampering:
   after a redeploy that changes the projection engine, a browser holding an old
   `dcf_input` mixes stale cash flows with a freshly computed WACC and returns a
   wrong answer with no error. The fix is for the endpoint to accept `drivers`
   and recompute server-side, so the DCF depends on nothing the client did.
2. **Concurrent-fetch race in `save_fetch`.** Two simultaneous requests for the
   same brand-new ticker can both try to insert the company row (read-then-insert).
   Harmless at current traffic; wants a unique constraint and an upsert.
3. **No Alembic.** `init_schema()` creates missing tables but never alters an
   existing one, so a column change today means touching the database by hand.
   Worth doing while the schema is still six tables.
4. **No rate limiting on the API.** Statements are cached for a week, so a model
   run costs one live quote call, and the universe guard caps the surface at 101
   tickers — but there is nothing stopping someone from looping it.
5. **IFRS filers are structurally out of reach.** The mapping spec is built
   around US GAAP statement shapes; the disqualifier is the accounting standard,
   not foreign domicile. Affected companies are excluded from the universe rather
   than reported as failures.

## Tech Stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy, pytest
- **Data** — PostgreSQL in production, SQLite locally; Financial Modeling Prep API
- **Frontend** — plain HTML/CSS/JavaScript, no framework and no build step
- **Infrastructure** — Railway (API, Postgres, cron), Cloudflare Workers (static
  frontend, DNS, TLS), GitHub Actions (CI)
- **Spec** — an Excel reference model, exported to `mappings.json` by
  `export_mappings.py` rather than transcribed by hand
- Hosting (planned): Railway