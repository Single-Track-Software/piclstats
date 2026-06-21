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
