"""Keyed /api/v1: auth, team scope, rate limit, and the origin-lock exception."""

from fastapi.testclient import TestClient

from piclstats.db import api_keys_store
from piclstats.web import api, edge
from piclstats.web.app import app
from piclstats.web.usage import should_log

KEY = {"id": 7, "name": "Lower Bucks news agent", "team_names": ["Lower Bucks Composite"]}


def _valid(monkeypatch):
    monkeypatch.setattr(api_keys_store, "lookup", lambda raw: KEY if raw == "pcls_good" else None)
    api._recent.clear()


def test_no_or_bad_key_is_401(monkeypatch):
    _valid(monkeypatch)
    c = TestClient(app)
    r = c.get("/api/v1/me")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer pcls_bad"}).status_code == 401
    assert c.get("/api/v1/me", headers={"Authorization": "pcls_good"}).status_code == 401


def test_valid_key_sees_its_teams(monkeypatch):
    _valid(monkeypatch)
    r = TestClient(app).get("/api/v1/me", headers={"Authorization": "Bearer pcls_good"})
    assert r.status_code == 200
    assert r.json() == {"key": "Lower Bucks news agent", "teams": ["Lower Bucks Composite"]}


def test_rate_limit_per_key():
    api._recent.clear()
    assert all(api._rate_ok(1, now=100.0) for _ in range(api.RATE_LIMIT))
    assert not api._rate_ok(1, now=100.5)
    assert api._rate_ok(2, now=100.5)  # other keys unaffected
    assert api._rate_ok(1, now=100.0 + api.RATE_WINDOW_SECONDS + 1)  # window slides


def test_api_passes_the_origin_lock_but_still_needs_a_key(monkeypatch):
    _valid(monkeypatch)
    monkeypatch.setattr(edge.settings, "origin_secret", "s3cret")
    c = TestClient(app)
    assert c.get("/robots.txt").status_code == 403  # the lock is on
    assert c.get("/api/v1/me").status_code == 401  # through the lock, refused by the key check
    assert c.get("/api/v1/me", headers={"Authorization": "Bearer pcls_good"}).status_code == 200


def test_public_openapi_docs_are_off():
    c = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


def test_keys_are_hashed_and_prefixed():
    assert api_keys_store.hash_key("pcls_x") != "pcls_x"
    assert len(api_keys_store.hash_key("pcls_x")) == 64
    assert api_keys_store.lookup("not-a-key") is None  # wrong prefix: no DB lookup


def test_api_calls_are_not_page_views():
    assert not should_log("GET", "/api/v1/me", 200)
    assert not should_log("GET", "/api/riders", 200)


def test_digest_is_scoped_to_the_keys_teams(monkeypatch):
    _valid(monkeypatch)
    c = TestClient(app)
    h = {"Authorization": "Bearer pcls_good"}
    assert c.get("/api/v1/teams/Some%20Other%20Team/digest", headers=h).status_code == 403
    assert c.get("/api/v1/teams/Lower%20Bucks%20Composite/digest").status_code == 401
    t = c.get("/api/v1/teams", headers=h).json()
    assert t["teams"][0]["name"] == "Lower Bucks Composite"
