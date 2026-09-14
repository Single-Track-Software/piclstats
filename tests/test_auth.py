"""Unit tests for password hashing and the role gates (no DB needed)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from piclstats.web import auth


def test_password_round_trip():
    h = auth.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # not stored in the clear
    assert auth.verify_password("correct horse battery staple", h)


def test_password_rejects_wrong():
    h = auth.hash_password("s3cret")
    assert not auth.verify_password("wrong", h)


def test_verify_password_handles_garbage_hash():
    # A malformed stored hash must fail closed, not raise.
    assert auth.verify_password("anything", "not-a-bcrypt-hash") is False


def test_long_password_does_not_raise():
    # bcrypt caps at 72 bytes; we truncate so >72-char passwords still work.
    pw = "a" * 200
    h = auth.hash_password(pw)
    assert auth.verify_password(pw, h)


class _FakeURL:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


class _FakeRequest:
    """Minimal stand-in for Starlette Request for gate logic."""

    def __init__(self, user=None, path="/staging", query=""):
        self._user = user
        self.url = _FakeURL(path, query)

    # load_user reads request.session via request.scope; bypass it by patching.


def test_require_member_redirects_anonymous(monkeypatch):
    monkeypatch.setattr(auth, "load_user", lambda request: None)
    with pytest.raises(auth.LoginRequired) as exc:
        auth.require_member(_FakeRequest(path="/staging", query="age_group=MS"))
    assert exc.value.next_path == "/staging?age_group=MS"


def test_require_member_allows_member(monkeypatch):
    user = {"id": 1, "role": "member", "is_active": True}
    monkeypatch.setattr(auth, "load_user", lambda request: user)
    assert auth.require_member(_FakeRequest()) is user


def test_require_admin_forbids_member(monkeypatch):
    user = {"id": 1, "role": "member", "is_active": True}
    monkeypatch.setattr(auth, "load_user", lambda request: user)
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(_FakeRequest())
    assert exc.value.status_code == 403


def test_require_admin_allows_admin(monkeypatch):
    user = {"id": 1, "role": "admin", "is_active": True}
    monkeypatch.setattr(auth, "load_user", lambda request: user)
    assert auth.require_admin(_FakeRequest()) is user


def test_require_member_api_401_anonymous(monkeypatch):
    monkeypatch.setattr(auth, "load_user", lambda request: None)
    with pytest.raises(HTTPException) as exc:
        auth.require_member_api(_FakeRequest())
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "raw,expected",
    [("/staging", "/staging"), ("//evil.com", "/"), ("https://evil.com", "/"), ("", "/")],
)
def test_safe_next(raw, expected):
    assert auth._safe_next(raw) == expected


# --- session secret guard ---------------------------------------------------


def _settings(monkeypatch, *, secret: str, https_only: bool):
    from piclstats.web import app as app_mod

    monkeypatch.setattr(app_mod.settings, "session_secret", secret, raising=False)
    monkeypatch.setattr(app_mod.settings, "session_https_only", https_only, raising=False)
    return app_mod


def test_missing_secret_in_prod_posture_refuses_to_start(monkeypatch):
    # The old code silently fell back to a hardcoded key here, which would let
    # anyone forge an admin session cookie.
    app_mod = _settings(monkeypatch, secret="", https_only=True)
    assert app_mod._insecure_session_config() is True
    with pytest.raises(RuntimeError, match="PICLSTATS_SESSION_SECRET"):
        app_mod._check_session_secret()


def test_missing_secret_allowed_for_local_http_dev(monkeypatch):
    app_mod = _settings(monkeypatch, secret="", https_only=False)
    assert app_mod._insecure_session_config() is False
    app_mod._check_session_secret()  # must not raise


def test_configured_secret_is_used_verbatim(monkeypatch):
    app_mod = _settings(monkeypatch, secret="deadbeef", https_only=True)
    assert app_mod._session_secret() == "deadbeef"
    app_mod._check_session_secret()


def test_absent_secret_falls_back_to_a_random_key(monkeypatch):
    # Not the old shared constant — two calls must differ.
    app_mod = _settings(monkeypatch, secret="", https_only=False)
    assert app_mod._session_secret() != app_mod._session_secret()


# --- client IP resolution (throttle keying) ---------------------------------


class _IPRequest:
    def __init__(self, headers: dict, peer: str | None = "10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": peer})() if peer else None


def test_client_ip_prefers_fly_header():
    req = _IPRequest({"fly-client-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.1"})
    assert auth.client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_first_forwarded_hop():
    req = _IPRequest({"x-forwarded-for": "198.51.100.1, 10.0.0.5"})
    assert auth.client_ip(req) == "198.51.100.1"


def test_client_ip_falls_back_to_socket_peer():
    assert auth.client_ip(_IPRequest({})) == "10.0.0.1"


def test_client_ip_handles_no_client():
    assert auth.client_ip(_IPRequest({}, peer=None)) is None
