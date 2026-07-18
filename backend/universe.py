# The supported ticker universe: the Nasdaq-100.
#
# Fetched from FMP rather than hardcoded. Index membership is reconstituted
# quarterly, so a hardcoded list silently goes stale — and a stale universe means
# the pipeline maintains companies that left the index while ignoring ones that
# joined.
#
# The list is cached to disk because it changes a few times a year, not per run.
# Pass refresh=True (or run this module directly) to re-pull it.
#
#   python universe.py            # show the cached universe
#   python universe.py --refresh  # re-pull from FMP and re-cache

import json
from datetime import datetime, timezone
from pathlib import Path

from fmp_test import fmp_get

CACHE_PATH = Path(__file__).resolve().parent / "universe.json"
ENDPOINT = "stable/nasdaq-constituent"


def fetch_universe():
    """Pull current Nasdaq-100 constituents from FMP. One API call."""
    rows = fmp_get(ENDPOINT)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"{ENDPOINT} returned no constituents: {str(rows)[:200]}")

    members = sorted(
        ({"symbol": r["symbol"], "name": r.get("name"), "sector": r.get("sector")}
         for r in rows if r.get("symbol")),
        key=lambda m: m["symbol"],
    )
    return {
        "source": ENDPOINT,
        "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "count": len(members),
        "members": members,
    }


def save_universe(data):
    CACHE_PATH.write_text(json.dumps(data, indent=2) + "\n")
    return CACHE_PATH


def load_universe(refresh=False):
    """Return the cached universe, pulling from FMP if missing or refresh=True."""
    if refresh or not CACHE_PATH.exists():
        data = fetch_universe()
        save_universe(data)
        return data
    return json.loads(CACHE_PATH.read_text())


def universe_symbols(refresh=False):
    """Just the ticker symbols, sorted.

    The Nasdaq-100 usually carries slightly more than 100 symbols because some
    companies have dual share classes (GOOG/GOOGL). Both are kept: they are
    separate symbols with separate share counts, so they value differently.
    """
    return [m["symbol"] for m in load_universe(refresh)["members"]]


def is_supported(ticker):
    """Whether a ticker is inside the supported universe."""
    return (ticker or "").strip().upper() in set(universe_symbols())


if __name__ == "__main__":
    import sys

    refresh = "--refresh" in sys.argv
    data = load_universe(refresh=refresh)
    print(f"{data['count']} constituents (fetched {data['fetched_at'][:19]}, "
          f"{'re-pulled' if refresh else 'cached'})")
    print(f"cache: {CACHE_PATH}")
    print()
    symbols = [m["symbol"] for m in data["members"]]
    for i in range(0, len(symbols), 12):
        print("  " + ", ".join(symbols[i:i + 12]))
