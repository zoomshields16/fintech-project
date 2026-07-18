# Cache-first data access. Everything in main.py fetches through here instead of
# calling get_financials() directly:
#
#   1. look for a recent complete fetch in the local log -> serve it, no network
#   2. otherwise hit FMP (Financial Modeling Prep), append the result to the log
#
# The returned dict is the same shape either way, so callers can't tell the
# difference — only the log knows.

from fmp_test import get_financials, extract_profile
from db_write import load_recent_fetch, save_fetch
from check_runner import run_checks_for_fetch
from restatement_detector import detect_restatements
from universe import is_supported

CACHE_MAX_AGE_HOURS = 24


class UnsupportedTicker(Exception):
    """Ticker is outside the supported universe (the Nasdaq-100)."""


def get_financials_cached(ticker, max_age_hours=CACHE_MAX_AGE_HOURS):
    """Returns (data, from_cache). Raises UnsupportedTicker outside the universe.

    The guard lives here rather than only in the frontend because this is the one
    path every fetch goes through. Without it, a typo or an unsupported symbol
    creates a company row and burns API calls on data the model cannot value.
    """
    if not is_supported(ticker):
        raise UnsupportedTicker(
            f"{ticker} is not in the Nasdaq-100, the supported universe.")

    cached = load_recent_fetch(ticker, max_age_hours=max_age_hours)
    if cached:
        return cached, True

    data = get_financials(ticker)
    profile = extract_profile(data.get("profile"))
    fetch_id = save_fetch(ticker, profile.get("company_name"), data)

    # Validate what we just ingested. Never let a check failure break the request —
    # the user still gets their data; the finding is recorded for us to look at.
    try:
        run_checks_for_fetch(fetch_id, data, ticker)
    except Exception as e:
        print(f"[checks] failed for {ticker} (fetch {fetch_id}): {e}")

    # Compare against the previous fetch of this company: did FMP change any
    # figure it had already reported? No-op on a company's first fetch.
    try:
        changed = detect_restatements(ticker)
        if changed:
            print(f"[restatements] {ticker}: {changed} historical figure(s) changed since last fetch")
    except Exception as e:
        print(f"[restatements] failed for {ticker} (fetch {fetch_id}): {e}")

    return data, False
