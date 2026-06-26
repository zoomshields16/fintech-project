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
    statement_params = {"period": "annual", "limit": 3}
    return {
        # Three statements (historical)
        "income_statement": fmp_get("stable/income-statement", ticker, statement_params),
        "balance_sheet": fmp_get("stable/balance-sheet-statement", ticker, statement_params),
        "cash_flow": fmp_get("stable/cash-flow-statement", ticker, statement_params),
        # Current snapshot + DCF inputs
        "profile": fmp_get("stable/profile", ticker),
        "enterprise_values": fmp_get("stable/enterprise-values", ticker),
        "treasury_rates": fmp_get("stable/treasury-rates"),
        "market_risk_premium": fmp_get("stable/market-risk-premium"),
        "levered_dcf": fmp_get("stable/levered-discounted-cash-flow", ticker),
    }


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: No API key found. Check your .env file.")
    else:
        print(f"API key loaded: {API_KEY[:6]}... (hidden)\n")
        ticker = "AAPL"
        data = get_financials(ticker)

        for name, result in data.items():
            count = len(result) if isinstance(result, list) else "?"
            print(f"{name}: {count} records")

        print(f"\n--- {ticker} spot check ---")
        profile = data["profile"][0]
        ev = data["enterprise_values"][0]
        treasury = data["treasury_rates"][0]
        print(f"Company: {profile.get('companyName')}")
        print(f"Current price: {profile.get('price')}")
        print(f"Beta: {profile.get('beta')}")
        print(f"Enterprise value: {ev.get('enterpriseValue')}")
        print(f"Shares outstanding: {ev.get('numberOfShares')}")
        print(f"10Y treasury: {treasury.get('year10')}")