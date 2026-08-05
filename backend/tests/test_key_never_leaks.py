# The API key must never appear in anything a caller can see.
#
# The key travels to FMP as a query parameter, so requests quotes it back inside
# its own exception messages ("Max retries exceeded with url: ...?apikey=KEY").
# main.py returns str(e) to the browser on a failed fetch and those endpoints
# need no authentication, so before the redaction in fmp_client an unreachable
# FMP would have handed the key to anyone who asked for a valuation.
#
# This is the kind of hole that only opens during an outage — exactly when
# nobody is reading test output — so it is pinned here.

import pytest
import requests

import fmp_client
from fmp_client import FMPRequestError

SENTINEL = "SENTINEL_KEY_do_not_leak_9f3a"


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(fmp_client, "API_KEY", SENTINEL)
    return SENTINEL


def test_redact_removes_the_key(fake_key):
    dirty = f"Max retries exceeded with url: /x?apikey={SENTINEL}&symbol=AAPL"
    assert SENTINEL not in fmp_client.redact(dirty)
    assert "***REDACTED***" in fmp_client.redact(dirty)


def test_connection_failure_does_not_expose_the_key(fake_key, monkeypatch):
    """The real shape of the bug: requests raises with the full URL in it."""
    def boom(*args, **kwargs):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: /stable/income-statement"
            f"?apikey={SENTINEL}&symbol=AAPL")

    monkeypatch.setattr(fmp_client.requests, "get", boom)

    with pytest.raises(FMPRequestError) as caught:
        fmp_client.fmp_get("stable/income-statement", "AAPL")

    assert SENTINEL not in str(caught.value)


def test_non_json_response_does_not_expose_the_key(fake_key, monkeypatch):
    """A rate-limit or outage page echoing the request URL back at us."""
    class Response:
        status_code = 429
        text = f"Rate limit hit for apikey={SENTINEL}"

        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr(fmp_client.requests, "get", lambda *a, **k: Response())

    with pytest.raises(FMPRequestError) as caught:
        fmp_client.fmp_get("stable/profile", "AAPL")

    assert SENTINEL not in str(caught.value)
    assert "429" in str(caught.value)


def test_requests_are_bounded_by_a_timeout(fake_key, monkeypatch):
    """A hung FMP connection must not hold a worker open forever."""
    seen = {}
    monkeypatch.setattr(fmp_client.requests, "get",
                        lambda *a, **k: seen.update(k) or _ok())

    fmp_client.fmp_get("stable/profile", "AAPL")
    assert seen.get("timeout") == fmp_client.REQUEST_TIMEOUT


def _ok():
    class Response:
        status_code = 200

        def json(self):
            return []
    return Response()
