from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from fmp_test import get_financials, extract_profile
from income_statement import pull_detail_accounts, compute_formula_lines, reconcile
from cash_flow import pull_cf_accounts, compute_cf_formula_lines, reconcile_cf
from balance_sheet import pull_bs_accounts, compute_bs_formula_lines, reconcile_bs
from projection_engine import project_income_statement, project_cash_flow, project_balance_sheet

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModelRequest(BaseModel):
    ticker: str


@app.get("/")
def home():
    return {"message": "Backend is running"}


def _avg_pct(numerators, denominators, n=4):
    """4-year average of abs(num)/abs(den), skipping None/zero pairs."""
    pairs = list(zip(numerators[:n], denominators[:n]))
    vals = [abs(a) / abs(b) for a, b in pairs if a and b]
    return round(sum(vals) / len(vals), 4) if vals else None


@app.post("/api/run-model")
def run_model(request: ModelRequest):
    ticker = request.ticker.upper()

    try:
        data = get_financials(ticker)
    except Exception as e:
        return {"error": f"Failed to fetch data for {ticker}: {str(e)}"}

    profile = extract_profile(data["profile"])

    income_years = []
    for record in data["income_statement"]:
        try:
            accounts = pull_detail_accounts(record)
            formulas = compute_formula_lines(accounts)
            checks = reconcile(record, formulas)
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
    for record in data["cash_flow"]:
        try:
            cf_accounts = pull_cf_accounts(record)
            cf_formulas = compute_cf_formula_lines(cf_accounts)
            cf_checks = reconcile_cf(record, cf_formulas)
        except Exception as e:
            cash_flow_years.append({"date": record.get("date"), "error": str(e)})
            continue
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
            # Investing
            "capex":                cf_accounts["capex"],
            "acquisitions":         cf_accounts["acquisitions"],
            "purchases_investments":cf_accounts["purchases_investments"],
            "sales_investments":    cf_accounts["sales_investments"],
            "other_investing":      cf_accounts["other_investing"],
            "investing_cf":         cf_formulas["investing_cf"],
            # Financing
            "long_term_debt":       cf_accounts["long_term_debt"],
            "short_term_debt":      cf_accounts["short_term_debt"],
            "stock_repurchased":    cf_accounts["stock_repurchased"],
            "stock_issued":         cf_accounts["stock_issued"],
            "dividends_paid":       cf_accounts["dividends_paid"],
            "other_financing":      cf_accounts["other_financing"],
            "financing_cf":         cf_formulas["financing_cf"],
            # Summary
            "fx_effect":            cf_accounts["fx_effect"],
            "net_change_cash":      cf_formulas["net_change_cash"],
            "cash_beginning":       cf_accounts["cash_beginning"],
            "cash_end":             cf_accounts["cash_end"],
            "free_cash_flow":       cf_formulas["free_cash_flow"],
            "reconcile":            cf_checks,
        })

    bs_years = []
    for record in data["balance_sheet"]:
        try:
            bs_accounts = pull_bs_accounts(record)
            bs_formulas = compute_bs_formula_lines(bs_accounts)
            bs_checks = reconcile_bs(record, bs_formulas)
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


class ProjectionRequest(BaseModel):
    ticker: str
    drivers: Drivers


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

    try:
        data = get_financials(ticker)
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

    last_is = pull_detail_accounts(last_is_raw)
    last_is_formulas = compute_formula_lines(last_is)
    last_is["pretax_income"]    = last_is_formulas["pretax_income"]
    last_is["net_income"]       = last_is_formulas["net_income"]
    last_is["gross_profit"]     = last_is_formulas["gross_profit"]
    last_is["operating_income"] = last_is_formulas["operating_income"]
    last_is["total_opex"]       = last_is_formulas["total_operating_expenses"]

    last_cf = pull_cf_accounts(last_cf_raw)
    last_cf_formulas = compute_cf_formula_lines(last_cf)
    last_cf["other_adjustments"] = last_cf_formulas["other_adjustments"]

    last_bs = pull_bs_accounts(last_bs_raw)

    # Derive base year from the most recent IS date
    date_str = last_is.get("date") or last_is_raw.get("date", "")
    base_year = int(date_str[:4]) if date_str else 2025

    proj_is = project_income_statement(last_is, drivers, base_year)
    proj_cf = project_cash_flow(proj_is, last_cf, drivers)
    proj_bs = project_balance_sheet(proj_is, proj_cf, last_bs)

    return {
        "ticker":           ticker,
        "income_statement": proj_is,
        "cash_flow":        proj_cf,
        "balance_sheet":    proj_bs,
    }