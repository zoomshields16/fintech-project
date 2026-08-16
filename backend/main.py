import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
from fmp_client import extract_profile
from data_source import get_financials_cached, live_price, UnsupportedTicker
from income_statement import pull_detail_accounts, compute_formula_lines, reconcile
from cash_flow import pull_cf_accounts, compute_cf_formula_lines, reconcile_cf
from balance_sheet import (pull_bs_accounts, compute_bs_formula_lines, reconcile_bs,
                           compute_equity_rollforward)
from projection_engine import (project_income_statement, project_cash_flow,
                               project_balance_sheet, project_working_capital,
                               working_capital_days, WC_HISTORY_YEARS)
from dcf_engine import compute_wacc, compute_ufcf, run_dcf, sensitivity_tables
import pipeline_status
from init_db import init_schema
from universe import universe_symbols, EXCLUDED_TICKERS


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A deployed host provisions an EMPTY database, and init_db.py is a manual
    # script that has only ever run on a laptop — without this every endpoint
    # fails on a fresh Postgres until someone connects and runs it by hand.
    # Creates only missing tables, so it is a no-op against the local app.db.
    init_schema()
    yield


app = FastAPI(lifespan=lifespan)

# A browser blocks a page on one origin from calling an API on another unless the
# API names that origin here, so the deployed frontend's domain has to be listed.
# Read from the environment rather than hardcoded, because the domain does not
# exist until the frontend is deployed — and needing a code change plus a redeploy
# to add it is exactly how this ends up set to "*" as a temporary fix. Comma
# separated; the localhost defaults keep the local workflow working unchanged.
DEFAULT_ORIGINS = "http://127.0.0.1:5500,http://localhost:5500"
ALLOWED_ORIGINS = [origin.strip()
                   for origin in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",")
                   if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModelRequest(BaseModel):
    ticker: str


@app.get("/")
def home():
    return {"message": "Backend is running"}


@app.get("/api/universe")
def universe_endpoint():
    """The supported ticker universe, so the frontend can reject a symbol before
    it costs anything.

    data_source enforces the same rule server-side and is the real guard — this
    exists so the user is told at the input box rather than after a page load,
    and so the list can never drift from the one the backend actually honours.
    """
    return {
        "index": "Nasdaq-100",
        "symbols": universe_symbols(),
        "excluded": sorted(EXCLUDED_TICKERS),
    }


def _avg_pct(numerators, denominators, n=4):
    """4-year average of abs(num)/abs(den), skipping None/zero pairs."""
    pairs = list(zip(numerators[:n], denominators[:n]))
    vals = [abs(a) / abs(b) for a, b in pairs if a and b]
    return round(sum(vals) / len(vals), 4) if vals else None


@app.post("/api/run-model")
def run_model(request: ModelRequest):
    ticker = request.ticker.upper()

    try:
        data, from_cache = get_financials_cached(ticker)
    except UnsupportedTicker as e:
        return {"error": str(e), "unsupported": True}
    except Exception as e:
        return {"error": f"Failed to fetch data for {ticker}: {str(e)}"}

    print(f"[run-model] {ticker}: {'CACHE HIT (no FMP call)' if from_cache else 'CACHE MISS -> fetched from FMP'}")

    profile = extract_profile(data["profile"])
    # The statements may be up to a week old; the price must not be.
    profile.update(live_price(ticker, fallback=profile))

    income_years = []
    is_records = data["income_statement"]
    for record in is_records:
        try:
            accounts = pull_detail_accounts(record, is_records, ticker)
            formulas = compute_formula_lines(accounts)
            checks = reconcile(record, formulas, is_records)
        except Exception as e:
            income_years.append({"date": record.get("date"), "error": str(e)})
            continue
        income_years.append({
            "date": accounts["date"],
            # Revenue block
            "revenue": accounts["revenue"],
            "cogs": accounts["cogs"],
            "gross_profit": formulas["gross_profit"],
            # OpEx block
            "sga": accounts["sga"],
            "rnd": accounts["rnd"],
            "other_opex": accounts["other_opex"],
            "total_opex": formulas["total_operating_expenses"],
            # EBIT
            "operating_income": formulas["operating_income"],
            # Below-the-line
            "interest_income": accounts["interest_income"],
            "interest_expense": accounts["interest_expense"],
            "other_income": accounts["other_income"],
            "pretax_income": formulas["pretax_income"],
            # Bottom
            "income_tax": accounts["income_tax"],
            "net_income": formulas["net_income"],
            # Per share
            "eps": accounts["eps"],
            "eps_diluted": accounts["eps_diluted"],
            "reconcile": checks,
        })

    cash_flow_years = []
    # Other financing is not displayed on the cash flow, but the equity
    # roll-forward below needs it (RSU tax withholding lands there), so keep it
    # aside by year rather than widening the response.
    cf_other_financing = {}
    cf_records = data["cash_flow"]
    for record in cf_records:
        try:
            cf_accounts = pull_cf_accounts(record, cf_records, ticker)
            cf_formulas = compute_cf_formula_lines(cf_accounts)
            cf_checks = reconcile_cf(record, cf_formulas, cf_records)
        except Exception as e:
            cash_flow_years.append({"date": record.get("date"), "error": str(e)})
            continue
        cf_other_financing[str(record.get("date", ""))[:4]] = cf_accounts["other_financing"]
        cash_flow_years.append({
            "date": record.get("date", ""),
            # Operating
            "net_income":           cf_accounts["net_income"],
            "depreciation":         cf_accounts["depreciation"],
            "stock_comp":           cf_accounts["stock_comp"],
            "other_adjustments":    cf_formulas["other_adjustments"],
            "change_ar":            cf_accounts["change_ar"],
            "change_inventory":     cf_accounts["change_inventory"],
            "change_ap":            cf_accounts["change_ap"],
            "other_wc":             cf_accounts["other_wc"],
            "operating_cf":         cf_formulas["operating_cf"],
            # Investing — modeled as capex only; the full FMP sum stays in the reconcile
            "capex":                cf_accounts["capex"],
            "investing_cf":         cf_formulas["investing_cf"],
            # Financing — modeled as buybacks + dividends only; full FMP sum stays in the reconcile
            "stock_repurchased":    cf_accounts["stock_repurchased"],
            "dividends_paid":       cf_accounts["dividends_paid"],
            "financing_cf":         cf_formulas["financing_cf"],
            # Summary — modeled change in cash; cash balances stay FMP actuals
            "net_change_cash":      cf_formulas["net_change_cash"],
            "cash_beginning":       cf_accounts["cash_beginning"],
            "cash_end":             cf_accounts["cash_end"],
            "free_cash_flow":       cf_formulas["free_cash_flow"],
            "reconcile":            cf_checks,
        })

    bs_years = []
    bs_records = data["balance_sheet"]
    for record in bs_records:
        try:
            bs_accounts = pull_bs_accounts(record, bs_records, ticker)
            bs_formulas = compute_bs_formula_lines(bs_accounts)
            bs_checks = reconcile_bs(record, bs_formulas, bs_records)
        except Exception as e:
            bs_years.append({"date": record.get("date"), "error": str(e)})
            continue
        bs_years.append({
            "date": record.get("date", ""),
            # Current Assets
            "cash_st_investments":     bs_accounts["cash_st_investments"],
            "accounts_receivable":     bs_accounts["accounts_receivable"],
            "inventory":               bs_accounts["inventory"],
            "prepaid":                 bs_accounts["prepaid"],
            "other_current":           bs_accounts["other_current"],
            "total_current_assets":    bs_formulas["total_current_assets"],
            # Non-Current Assets
            "ppe_net":                 bs_accounts["ppe_net"],
            "goodwill":                bs_accounts["goodwill"],
            "intangible_assets":       bs_accounts["intangible_assets"],
            "lt_investments":          bs_accounts["lt_investments"],
            "other_noncurrent":        bs_accounts["other_noncurrent"],
            "total_noncurrent_assets": bs_formulas["total_noncurrent_assets"],
            "total_assets":            bs_formulas["total_assets"],
            # Current Liabilities
            "accounts_payable":        bs_accounts["accounts_payable"],
            "accrued_expenses":        bs_accounts["accrued_expenses"],
            "current_ltd":             bs_accounts["current_ltd"],
            "other_current_liab":      bs_accounts["other_current_liab"],
            "total_current_liab":      bs_formulas["total_current_liab"],
            # Non-Current Liabilities
            "long_term_debt":          bs_accounts["long_term_debt"],
            "deferred_tax_liab":       bs_accounts["deferred_tax_liab"],
            "other_lt_liab":           bs_accounts["other_lt_liab"],
            "unearned_revenue_lt":     bs_accounts["unearned_revenue_lt"],
            "total_noncurrent_liab":   bs_formulas["total_noncurrent_liab"],
            "total_liabilities":       bs_formulas["total_liabilities"],
            # Shareholders' Equity
            "common_stock":            bs_accounts["common_stock"],
            "apic":                    bs_accounts["apic"],
            "retained_earnings":       bs_accounts["retained_earnings"],
            "treasury_stock":          bs_accounts["treasury_stock"],
            "aoci":                    bs_accounts["aoci"],
            "other_equity":            bs_accounts["other_equity"],
            "total_equity":            bs_formulas["total_equity"],
            "total_lae":               bs_formulas["total_lae"],
            "check_balance":           bs_formulas["check_balance"],
            "reconcile":               bs_checks,
        })

    # Equity roll-forward — explains each year's change in total equity. Needs all
    # three statements, so it runs once the per-statement loops are done. Historical
    # years only; OCI is never projected.
    is_by_year = {str(y.get("date", ""))[:4]: y for y in income_years if "error" not in y}
    cf_by_year = {str(y.get("date", ""))[:4]: y for y in cash_flow_years if "error" not in y}
    for i, bs_year in enumerate(bs_years):
        if "error" in bs_year:
            continue
        year = str(bs_year.get("date", ""))[:4]
        prev = bs_years[i + 1] if (i + 1) < len(bs_years) else None
        inc, cf_year = is_by_year.get(year), cf_by_year.get(year)
        # The oldest year has no prior balance sheet to roll from.
        if not prev or "error" in prev or not inc or not cf_year:
            bs_year["equity_rollforward"] = None
            continue
        bs_year["equity_rollforward"] = compute_equity_rollforward(
            prev_equity=prev["total_equity"],
            prev_aoci=prev["aoci"],
            curr_equity=bs_year["total_equity"],
            curr_aoci=bs_year["aoci"],
            net_income=inc.get("net_income"),
            dividends_paid=cf_year.get("dividends_paid"),
            stock_repurchased=cf_year.get("stock_repurchased"),
            stock_comp=cf_year.get("stock_comp"),
            other_financing=cf_other_financing.get(year),
        )

    valid_is = [y for y in income_years if "error" not in y]
    valid_cf = [y for y in cash_flow_years if "error" not in y]
    is_rev = [y.get("revenue") for y in valid_is]

    suggested_drivers = {
        "rnd_pct":     _avg_pct([y.get("rnd") for y in valid_is], is_rev),
        "da_pct":      _avg_pct([y.get("depreciation") for y in valid_cf], is_rev),
        "tax_rate":    _avg_pct([y.get("income_tax") for y in valid_is],
                                [y.get("pretax_income") for y in valid_is]),
        "sbc_pct":     _avg_pct([y.get("stock_comp") for y in valid_cf], is_rev),
        "buyback_pct": _avg_pct([abs(y.get("stock_repurchased") or 0) for y in valid_cf], is_rev),
    }

    return {
        "ticker": ticker,
        "company_name": profile["company_name"],
        "stock_price": profile["stock_price"],
        "income_statement": income_years,
        "cash_flow": cash_flow_years,
        "balance_sheet": bs_years,
        "suggested_drivers": suggested_drivers,
    }


class Drivers(BaseModel):
    # Group 1 — per-year lists (5 values each)
    revenue_growth: List[float]
    cogs_pct: List[float]
    sga_pct: List[float]
    capex_pct: List[float]
    # Group 2 — per-year lists (5 values); None = fall back to last actual year
    rnd_pct:     Optional[List[float]] = None
    da_pct:      Optional[List[float]] = None
    tax_rate:    Optional[List[float]] = None
    sbc_pct:     Optional[List[float]] = None
    buyback_pct: Optional[List[float]] = None


class CaseDrivers(BaseModel):
    """Group 1 only — the four drivers Carson's case switch chooses between."""
    revenue_growth: List[float]
    cogs_pct: List[float]
    sga_pct: List[float]
    capex_pct: List[float]


# Carson runs one global switch (Drivers!C7) that INDEXes each Group 1 driver block:
# 1 = best, 2 = base, 3 = worst — Drivers!C11:C13 for revenue growth and the parallel
# blocks at 19:21 (COGS), 27:29 (SG&A), 35:37 (CapEx). Model!G11:G14 read the switched
# row. Group 2 is deliberately NOT in here: R&D, D&A, tax, SBC and buybacks come off
# their own Automatic/Manual block (Drivers!F53, F60, F67, F74, F80), which the case
# switch does not touch, so one set of them is shared across all three cases.
CASE_NAMES = ("best", "base", "worst")
DEFAULT_CASE = "base"


class ProjectionRequest(BaseModel):
    ticker: str
    drivers: Drivers
    # Optional. Given cases, each one's Group 1 drivers are run against the shared
    # Group 2 drivers above and every case comes back in a single response. Absent,
    # the endpoint behaves exactly as it did before: one projection from `drivers`.
    cases: Optional[Dict[str, CaseDrivers]] = None


@app.post("/api/run-projection")
def run_projection(request: ProjectionRequest):
    ticker = request.ticker.upper()
    drivers = {
        "revenue_growth": request.drivers.revenue_growth,
        "cogs_pct":       request.drivers.cogs_pct,
        "sga_pct":        request.drivers.sga_pct,
        "capex_pct":      request.drivers.capex_pct,
        "rnd_pct":        request.drivers.rnd_pct,
        "da_pct":         request.drivers.da_pct,
        "tax_rate":       request.drivers.tax_rate,
        "sbc_pct":        request.drivers.sbc_pct,
        "buyback_pct":    request.drivers.buyback_pct,
    }

    if request.cases:
        unknown = sorted(set(request.cases) - set(CASE_NAMES))
        if unknown:
            return {"error": f"Unknown case(s): {', '.join(unknown)}. "
                             f"Expected any of {', '.join(CASE_NAMES)}."}

    try:
        data, _ = get_financials_cached(ticker)
    except UnsupportedTicker as e:
        return {"error": str(e), "unsupported": True}
    except Exception as e:
        return {"error": f"Failed to fetch data for {ticker}: {str(e)}"}

    # Build last actual year from the most recent IS, CF, BS records
    is_records  = data["income_statement"]
    cf_records  = data["cash_flow"]
    bs_records  = data["balance_sheet"]

    if not is_records or not cf_records or not bs_records:
        return {"error": "Insufficient historical data to project"}

    # Pull most recent actual year for each statement
    last_is_raw = is_records[0]
    last_cf_raw = cf_records[0]
    last_bs_raw = bs_records[0]

    last_is = pull_detail_accounts(last_is_raw, is_records, ticker)
    last_is_formulas = compute_formula_lines(last_is)
    last_is["pretax_income"]    = last_is_formulas["pretax_income"]
    last_is["net_income"]       = last_is_formulas["net_income"]
    last_is["gross_profit"]     = last_is_formulas["gross_profit"]
    last_is["operating_income"] = last_is_formulas["operating_income"]
    last_is["total_opex"]       = last_is_formulas["total_operating_expenses"]

    last_cf = pull_cf_accounts(last_cf_raw, cf_records, ticker)
    last_cf_formulas = compute_cf_formula_lines(last_cf)
    last_cf["other_adjustments"] = last_cf_formulas["other_adjustments"]

    last_bs = pull_bs_accounts(last_bs_raw, bs_records, ticker)

    # Derive base year from the most recent IS date
    date_str = last_is.get("date") or last_is_raw.get("date", "")
    base_year = int(date_str[:4]) if date_str else 2025

    # Working capital comes first: the cash flow's AR/inventory/AP movements are
    # derived from the change in these balances, the way Carson wires it, rather than
    # repeating the last actual year's movement forever. The days ratios are averaged
    # over recent history, so they need the mapped historical years, not just the last.
    is_history = [pull_detail_accounts(r, is_records, ticker)
                  for r in is_records[:WC_HISTORY_YEARS]]
    bs_history = [pull_bs_accounts(r, bs_records, ticker)
                  for r in bs_records[:WC_HISTORY_YEARS]]
    wc_days = working_capital_days(is_history, bs_history)

    def project(driver_set):
        """One full three-statement forecast. Everything above this point — the fetch,
        the mapping, the working-capital days — is case-independent, so running three
        cases costs three passes of arithmetic and no extra API calls."""
        proj_is = project_income_statement(last_is, driver_set, base_year)
        proj_wc = project_working_capital(proj_is, wc_days)
        proj_cf = project_cash_flow(proj_is, last_cf, driver_set,
                                    projected_wc=proj_wc, last_bs_actuals=last_bs)
        proj_bs = project_balance_sheet(proj_is, proj_cf, last_bs, projected_wc=proj_wc)
        return {
            "income_statement": proj_is,
            "cash_flow":        proj_cf,
            "balance_sheet":    proj_bs,
            "ufcf":             compute_ufcf(proj_is, proj_cf, proj_bs, last_bs),
        }

    result = {"ticker": ticker}

    if not request.cases:
        result.update(project(drivers))
        return result

    # Each case swaps in its own Group 1 drivers over the shared Group 2 set.
    result["cases"] = {name: project({**drivers, **case.model_dump()})
                       for name, case in request.cases.items()}
    # The top-level statement keys mirror one case, so anything that read this
    # response before cases existed still finds what it expects.
    selected = result["cases"].get(DEFAULT_CASE) or next(iter(result["cases"].values()))
    result.update(selected)
    return result


class DCFRequest(BaseModel):
    ticker: str
    ufcf: List[float]                         # 5 projected UFCF values
    terminal_growth_rate: float = 0.025
    # Optional user overrides — backend computes defaults from FMP if absent
    wacc_override:             Optional[float] = None
    risk_free_rate_override:   Optional[float] = None
    beta_override:             Optional[float] = None
    market_risk_premium_override: Optional[float] = None
    pre_tax_cost_debt_override: Optional[float] = None
    tax_rate_override:         Optional[float] = None


@app.post("/api/run-dcf")
def run_dcf_endpoint(request: DCFRequest):
    ticker = request.ticker.upper()
    try:
        data, _ = get_financials_cached(ticker)
    except UnsupportedTicker as e:
        return {"error": str(e), "unsupported": True}
    except Exception as e:
        return {"error": f"Failed to fetch data for {ticker}: {str(e)}"}

    profile   = extract_profile(data["profile"])
    # Live, not cached: this price sets the premium/discount the DCF reports against.
    profile.update(live_price(ticker, fallback=profile))
    ev_data   = data.get("enterprise_values", [])
    treas     = data.get("treasury_rates", [])
    mrp_data  = data.get("market_risk_premium", [])
    is_recs   = data.get("income_statement", [])
    bs_recs_raw = data.get("balance_sheet", [])

    # --- FMP inputs ---
    ev_raw          = ev_data[0] if ev_data else {}
    treas_raw       = treas[0]   if treas   else {}
    mrp_match       = next((r for r in mrp_data if r.get("country") == "United States"), {})

    shares_outstanding = ev_raw.get("numberOfShares") or 0
    cash               = ev_raw.get("minusCashAndCashEquivalents") or 0
    total_debt         = ev_raw.get("addTotalDebt") or 0
    stock_price        = profile.get("stock_price") or 0
    market_cap         = profile.get("market_cap") or (stock_price * shares_outstanding)
    beta               = profile.get("beta") or 1.0

    # Risk-free rate: 10Y Treasury (stored as percentage in FMP)
    rf_raw = treas_raw.get("year10") or treas_raw.get("tenYear") or 0
    risk_free_rate = rf_raw / 100 if rf_raw > 1 else rf_raw  # normalize if stored as 4.38 vs 0.0438

    mrp = mrp_match.get("totalEquityRiskPremium") or 0.0475  # default 4.75% if FMP returns None

    # Approximate pre-tax cost of debt: interest_expense / total_debt
    pre_tax_cost_debt = 0.04  # default 4%
    if is_recs and total_debt and total_debt > 0:
        avg_interest = 0.0
        count = 0
        for rec in is_recs[:4]:
            ie = abs(rec.get("interestExpense") or 0)
            if ie > 0:
                avg_interest += ie
                count += 1
        if count > 0:
            pre_tax_cost_debt = (avg_interest / count) / total_debt

    # Effective tax rate from most recent IS
    effective_tax = 0.21
    if is_recs:
        pt = is_recs[0].get("incomeBeforeTax") or 0
        tx = abs(is_recs[0].get("incomeTaxExpense") or 0)
        if pt and pt > 0:
            effective_tax = tx / pt

    # Apply user overrides
    risk_free_rate   = request.risk_free_rate_override    if request.risk_free_rate_override    is not None else risk_free_rate
    beta             = request.beta_override              if request.beta_override              is not None else beta
    mrp              = request.market_risk_premium_override if request.market_risk_premium_override is not None else mrp
    pre_tax_cost_debt = request.pre_tax_cost_debt_override if request.pre_tax_cost_debt_override is not None else pre_tax_cost_debt
    effective_tax    = request.tax_rate_override          if request.tax_rate_override          is not None else effective_tax

    # Compute WACC (or use override)
    wacc_components = compute_wacc(
        beta, risk_free_rate, mrp,
        total_debt, market_cap, pre_tax_cost_debt, effective_tax,
    )
    wacc = request.wacc_override if request.wacc_override is not None else wacc_components.get("wacc", 0.08)

    net_debt = total_debt - cash  # negative = net cash position

    # --- Interest Rate Schedule ---
    recent_bs_raw  = bs_recs_raw[0] if bs_recs_raw else {}
    current_ltd_bs = recent_bs_raw.get("shortTermDebt") or 0
    lt_debt_bs     = recent_bs_raw.get("longTermDebt") or 0
    lt_leases_bs   = recent_bs_raw.get("capitalLeaseObligations") or 0
    total_debt_bs  = current_ltd_bs + lt_debt_bs + lt_leases_bs

    interest_history = []
    for i, is_rec in enumerate(is_recs[:5]):
        year_str   = (is_rec.get("date") or "")[:4]
        bs_rec     = bs_recs_raw[i]     if i < len(bs_recs_raw)     else {}
        bs_prev    = bs_recs_raw[i + 1] if (i + 1) < len(bs_recs_raw) else {}
        end_debt   = (bs_rec.get("longTermDebt")  or 0) + (bs_rec.get("shortTermDebt") or 0)
        begin_debt = (bs_prev.get("longTermDebt") or 0) + (bs_prev.get("shortTermDebt") or 0)
        avg_debt   = (begin_debt + end_debt) / 2 if (begin_debt + end_debt) > 0 else 0
        int_exp    = abs(is_rec.get("interestExpense") or 0)
        impl_rate  = int_exp / avg_debt if avg_debt > 0 else None
        interest_history.append({
            "year":             year_str,
            "beginning_debt":   begin_debt,
            "ending_debt":      end_debt,
            "avg_debt":         avg_debt,
            "interest_expense": int_exp,
            "implied_rate":     impl_rate,
        })

    # Base DCF
    tgr  = request.terminal_growth_rate
    ufcf = request.ufcf

    dcf_result = run_dcf(ufcf, wacc, tgr, shares_outstanding, net_debt)
    sens       = sensitivity_tables(ufcf, wacc, shares_outstanding, net_debt, stock_price)

    premium = None
    if stock_price and dcf_result.get("equity_value_per_share"):
        premium = (dcf_result["equity_value_per_share"] / stock_price) - 1

    return {
        "ticker":        ticker,
        "company_name":  profile.get("company_name"),
        "stock_price":   stock_price,
        # WACC inputs
        "wacc_inputs": {
            "risk_free_rate":     risk_free_rate,
            "beta":               beta,
            "market_risk_premium": mrp,
            "pre_tax_cost_debt":  pre_tax_cost_debt,
            "tax_rate":           effective_tax,
            "total_debt":         total_debt,
            "market_cap":         market_cap,
            **wacc_components,
            "wacc":               wacc,
        },
        # EV bridge
        "shares_outstanding": shares_outstanding,
        "cash":               cash,
        "total_debt":         total_debt,
        "net_debt":           net_debt,
        # DCF schedule
        "ufcf":                    ufcf,
        "terminal_growth_rate":    tgr,
        **dcf_result,
        "premium_discount":        premium,
        # Sensitivity
        "sensitivity": sens,
        # Interest Rate Schedule
        "interest_rate_schedule": {
            "current_ltd":            current_ltd_bs,
            "lt_debt":                lt_debt_bs,
            "lt_leases":              lt_leases_bs,
            "total_debt":             total_debt_bs,
            "cash":                   cash,
            "net_debt":               net_debt,
            "history":                interest_history,
            "pre_tax_cost_debt_used": pre_tax_cost_debt,
        },
    }

# ── Pipeline health ────────────────────────────────────────────────────────
# The quality layers write to the database on every fetch, but nothing surfaced
# what they found — a restatement could sit in a table for months. These read it
# back out so the status page (and a banner in the workbench) can show it.


# Only the write endpoint is protected, and that asymmetry is deliberate.
#
# The GETs stay open because this project exists to be shown to people: a status
# page that cannot be opened without a secret demonstrates nothing, and what it
# publishes — pass rates and job history derived from public companies' filings
# — is not sensitive.
#
# The POST is different in kind. It is the only call that changes state, and
# what it changes is triage. Marking a restatement reviewed removes it from the
# queue, so left open, anyone who found the URL could clear every finding and
# the alerting would then look healthy exactly when it had stopped working. A
# silent failure of the thing built to prevent silent failures.
STATUS_API_KEY = os.getenv("STATUS_API_KEY")


def require_api_key(x_api_key: Optional[str] = Header(None)):
    # Fails closed when the variable is unset, rather than falling back to open.
    # A deploy that forgets it then rejects writes loudly instead of accepting
    # anonymous ones, which would be indistinguishable from having no auth.
    if not STATUS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="STATUS_API_KEY is not configured on the server.")
    # compare_digest, not ==: a plain string comparison returns as soon as two
    # characters differ, so how long it takes leaks how much of the key is right.
    if not x_api_key or not hmac.compare_digest(x_api_key, STATUS_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


class ReviewRequest(BaseModel):
    ids: List[int]
    reviewed: bool = True


@app.get("/api/pipeline-status")
def pipeline_status_endpoint():
    """Everything the status page needs: coverage, pass rates, worst lines,
    unreviewed restatements, recent job runs."""
    try:
        return pipeline_status.full_status()
    except Exception as e:
        return {"error": f"Failed to read pipeline status: {str(e)}"}


@app.get("/api/restatements")
def restatements_endpoint(ticker: Optional[str] = None,
                          only_unreviewed: bool = True,
                          limit: int = 100):
    """Restated figures, optionally scoped to one ticker. Drives the workbench
    banner ("FMP changed N figures for AAPL since our last pull")."""
    try:
        return {
            "counts": pipeline_status.restatement_counts(ticker),
            "restatements": pipeline_status.restatements(
                ticker=ticker, only_unreviewed=only_unreviewed, limit=limit),
        }
    except Exception as e:
        return {"error": f"Failed to read restatements: {str(e)}"}


@app.post("/api/restatements/review", dependencies=[Depends(require_api_key)])
def review_restatements(request: ReviewRequest):
    """Mark findings reviewed (or un-review them) so the queue can be worked down."""
    try:
        changed = pipeline_status.mark_reviewed(request.ids, reviewed=request.reviewed)
        return {"updated": changed, "reviewed": request.reviewed}
    except Exception as e:
        return {"error": f"Failed to update restatements: {str(e)}"}
