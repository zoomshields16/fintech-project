# Cache-first data access. Everything in main.py fetches through here instead of
# calling get_financials() directly:
#
#   1. look for a recent complete batch in the local log -> serve it, no network
#   2. otherwise hit FMP (Financial Modeling Prep), append the result to the log
#
# The returned dict is the same shape either way, so callers can't tell the
# difference — only the log knows.

from fmp_test import get_financials, extract_profile
from db_write import load_recent_pull, save_raw_pulls

CACHE_MAX_AGE_HOURS = 24


def get_financials_cached(ticker, max_age_hours=CACHE_MAX_AGE_HOURS):
    """Returns (data, from_cache)."""
    cached = load_recent_pull(ticker, max_age_hours=max_age_hours)
    if cached:
        return cached, True

    data = get_financials(ticker)
    profile = extract_profile(data.get("profile"))
    save_raw_pulls(ticker, profile.get("company_name"), data)
    return data, False
