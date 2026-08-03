# The write endpoint's auth.
#
# Worth a test rather than a manual check, because the failure is invisible: if
# the dependency is dropped in a refactor, every one of these calls still
# returns 200 and the app looks entirely healthy. Nothing surfaces the hole
# until someone clears the restatement queue anonymously.
#
# The read endpoints are asserted OPEN on purpose. That is a decision, not an
# oversight — the status page is meant to be shown to people — so it is pinned
# here too, and a future change that quietly locks them down has to say so.

import pytest
from fastapi.testclient import TestClient

import main

BODY = {"ids": [], "reviewed": True}
REVIEW = "/api/restatements/review"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(main, "STATUS_API_KEY", "test-key")
    return "test-key"


def test_read_endpoints_need_no_key(client):
    assert client.get("/api/pipeline-status").status_code == 200
    assert client.get("/api/restatements").status_code == 200


def test_write_rejects_missing_key(client, key):
    assert client.post(REVIEW, json=BODY).status_code == 401


def test_write_rejects_wrong_key(client, key):
    res = client.post(REVIEW, json=BODY, headers={"X-API-Key": "not-the-key"})
    assert res.status_code == 401


def test_write_accepts_correct_key(client, key):
    res = client.post(REVIEW, json=BODY, headers={"X-API-Key": key})
    assert res.status_code == 200


def test_write_fails_closed_when_server_has_no_key(client, monkeypatch):
    """An unset key must refuse writes, not wave them through.

    The opposite default is the dangerous one: a deploy that forgets the
    variable would accept anonymous writes while looking perfectly fine.
    """
    monkeypatch.setattr(main, "STATUS_API_KEY", None)
    res = client.post(REVIEW, json=BODY, headers={"X-API-Key": "anything"})
    assert res.status_code == 503
