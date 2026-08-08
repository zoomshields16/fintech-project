# The price is fetched live while the statements are served from cache, so these
# pin the two things that would otherwise fail quietly: that a live quote actually
# overrides the cached profile, and that a failed quote degrades to the stale price
# instead of taking the whole valuation down with it.

import data_source


CACHED = {"company_name": "Apple Inc.", "stock_price": 100.0, "market_cap": 1_000}


def test_live_quote_overrides_the_cached_price(monkeypatch):
    monkeypatch.setattr(data_source, "get_quote",
                        lambda t: {"stock_price": 313.33, "market_cap": 4_600})

    assert data_source.live_price("AAPL", fallback=CACHED) == {
        "stock_price": 313.33, "market_cap": 4_600}


def test_quote_failure_falls_back_to_the_cached_price(monkeypatch):
    def boom(ticker):
        raise RuntimeError("FMP unreachable")

    monkeypatch.setattr(data_source, "get_quote", boom)

    # A stale price beats no page at all.
    assert data_source.live_price("AAPL", fallback=CACHED) == CACHED


def test_empty_quote_falls_back_field_by_field(monkeypatch):
    # FMP answering with nulls is not the same as FMP being down, and it used to
    # be the case that reached the model as a price of None.
    monkeypatch.setattr(data_source, "get_quote",
                        lambda t: {"stock_price": None, "market_cap": None})

    assert data_source.live_price("AAPL", fallback=CACHED) == {
        "stock_price": 100.0, "market_cap": 1_000}


def test_no_fallback_available_is_not_an_error(monkeypatch):
    monkeypatch.setattr(data_source, "get_quote", lambda t: {})

    assert data_source.live_price("AAPL") == {
        "stock_price": None, "market_cap": None}


def test_cache_window_matches_the_refresh_cadence():
    # The scheduled job refreshes on a 6-day cycle; a shorter cache window here
    # would make page views re-pull all eight endpoints behind its back.
    assert data_source.CACHE_MAX_AGE_HOURS == 24 * 7
