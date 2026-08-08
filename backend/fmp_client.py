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

# Without this a hung FMP connection holds a worker open indefinitely. Locally
# that is a stuck terminal; on a deployed host it is a worker that never returns.
REQUEST_TIMEOUT = 20


class FMPRequestError(RuntimeError):
    """A network-level failure talking to FMP, with the API key stripped out."""


def redact(text):
    """Remove the API key from a string.

    The key travels as a query parameter, so requests' own exception messages
    quote it back in full — "Max retries exceeded with url: ...?apikey=KEY".
    main.py returns str(e) to the browser on a failed fetch, and those endpoints
    need no authentication, so an unreachable FMP would have handed the key to
    anyone who asked. Proven, not theorised: a ConnectionError raised here does
    contain it.

    Redacting at the boundary that owns the key is the one place this can be
    fixed once rather than at every caller that might echo an error.
    """
    text = str(text)
    return text.replace(API_KEY, "***REDACTED***") if API_KEY else text


def fmp_get(endpoint, ticker=None, extra_params=None):
    url = f"{BASE_URL}/{endpoint}"
    params = {"apikey": API_KEY}
    if ticker:
        params["symbol"] = ticker
    if extra_params:
        params.update(extra_params)

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        # `from None` on purpose: the original exception carries the unredacted
        # URL in its own message, and a chained traceback would print it into
        # the host's logs.
        raise FMPRequestError(f"{endpoint} request failed: {redact(e)}") from None

    try:
        return response.json()
    except ValueError:
        # A non-JSON body means an outage page or a rate-limit notice. Surface
        # the status and a short excerpt rather than a bare parse error.
        raise FMPRequestError(
            f"{endpoint} returned {response.status_code}, not JSON: "
            f"{redact(response.text)[:200]}") from None


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


def get_quote(ticker):
    """Current price and market cap for one ticker. ONE API call.

    Split out from get_financials because price and financials go stale on
    completely different clocks. Statements change quarterly and are served from
    the fetch cache for up to a week; a share price is wrong within the minute.
    Bundling them meant the only way to freshen a price was to re-pull all eight
    endpoints, so the cache window was really a staleness budget for the price.
    """
    quote = fmp_get("stable/quote", ticker)
    q = quote[0] if isinstance(quote, list) and quote else {}
    return {
        "stock_price": q.get("price"),
        "market_cap": q.get("marketCap"),
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