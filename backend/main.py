from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fmp_test import get_financials, extract_profile
from income_statement import pull_detail_accounts, compute_formula_lines, reconcile
from cash_flow import pull_cf_accounts, compute_cf_formula_lines, reconcile_cf

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

    return {
        "ticker": ticker,
        "company_name": profile["company_name"],
        "stock_price": profile["stock_price"],
        "income_statement": income_years,
        "cash_flow": cash_flow_years,
    }