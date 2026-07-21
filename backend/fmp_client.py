# The FMP (Financial Modeling Prep) HTTP client — the only module that talks to
# the network. Everything else reaches FMP through data_source.py, which checks
# the local log first, so a call from here always means a real API request.
#
# Nothing is interpreted here: responses are returned as FMP sent them, and the
# mapping engine decides what the fields mean. That separation is what lets
# api_responses store the raw payload and be re-read years later.

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com"


def fmp_get(endpoint, ticker=None, extra_params=None):
    url = f"{BASE_URL}/{endpoint}"
    params = {"apikey": API_KEY}
    if ticker:
        params["symbol"] = ticker
    if extra_params:
        params.update(extra_params)
    response = requests.get(url, params=params)
    return response.json()


def get_financials(ticker):
    statement_params = {"period": "annual", "limit": 10}
    return {
        "income_statement": fmp_get("stable/income-statement", ticker, statement_params),
        "balance_sheet": fmp_get("stable/balance-sheet-statement", ticker, statement_params),
        "cash_flow": fmp_get("stable/cash-flow-statement", ticker, statement_params),
        "profile": fmp_get("stable/profile", ticker),
        "enterprise_values": fmp_get("stable/enterprise-values", ticker),
        "treasury_rates": fmp_get("stable/treasury-rates"),
        "market_risk_premium": fmp_get("stable/market-risk-premium"),
        "levered_dcf": fmp_get("stable/levered-discounted-cash-flow", ticker),
    }


def extract_profile(profile_data):
    p = profile_data[0] if profile_data else {}
    return {
        "company_name": p.get("companyName"),
        "currency": p.get("currency"),
        "sector": p.get("sector"),
        "industry": p.get("industry"),
        "stock_price": p.get("price"),
        "market_cap": p.get("marketCap"),
        "beta": p.get("beta"),
    }


def extract_enterprise_values(ev_data):
    e = ev_data[0] if ev_data else {}
    return {
        "enterprise_value": e.get("enterpriseValue"),
        "ev_market_cap": e.get("marketCapitalization"),
        "ev_stock_price": e.get("stockPrice"),
        "shares_outstanding": e.get("numberOfShares"),
        "cash_and_equivalents": e.get("minusCashAndCashEquivalents"),
        "total_debt": e.get("addTotalDebt"),
    }


def extract_treasury(treasury_data):
    t = treasury_data[0] if treasury_data else {}
    return {
        "treasury_10y": t.get("year10"),
        "treasury_30y": t.get("year30"),
        "treasury_date": t.get("date"),
    }


def extract_market_risk_premium(mrp_data, country="United States"):
    match = next((r for r in mrp_data if r.get("country") == country), {})
    return {
        "market_risk_premium": match.get("totalEquityRiskPremium"),
        "country_risk_premium": match.get("countryRiskPremium"),
    }


def extract_dcf_inputs(data):
    return {
        **extract_profile(data["profile"]),
        **extract_enterprise_values(data["enterprise_values"]),
        **extract_treasury(data["treasury_rates"]),
        **extract_market_risk_premium(data["market_risk_premium"]),
    }


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: No API key found. Check your .env file.")
    else:
        print(f"API key loaded: {API_KEY[:6]}... (hidden)\n")
        ticker = "AAPL"
        data = get_financials(ticker)
        dcf_inputs = extract_dcf_inputs(data)

        print(f"--- {ticker} DCF inputs ({len(dcf_inputs)} values) ---")
        for key, value in dcf_inputs.items():
            print(f"{key}: {value}")