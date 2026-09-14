"""Unit tests for invite/reset tokens, password rules, and the mail client.

The full request walk (admin invites → coach sets password → signs in → resets)
is exercised against a real Postgres separately; these cover the pieces that
must hold regardless of wiring, with no database.
"""

from __future__ import annotations

import logging

import pytest

from piclstats.db import tokens_store
from piclstats.web import auth, mail


# ── Token hashing ────────────────────────────────────────────────────


def test_token_hash_is_stable_and_not_the_token():
    token = "some-raw-token"
    digest = tokens_store.hash_token(token)
    assert digest == tokens_store.hash_token(token)
    assert token not in digest
    assert len(digest) == 64  # sha256 hex


def test_different_tokens_hash_differently():
    assert tokens_store.hash_token("a") != tokens_store.hash_token("b")


def test_invites_outlive_resets():
    # Invites sit in an inbox over a weekend; resets answer "locked out now".
    assert tokens_store.INVITE_TTL > tokens_store.RESET_TTL


def test_purposes_are_distinct():
    assert tokens_store.INVITE != tokens_store.RESET


# ── Password rules ───────────────────────────────────────────────────


def test_mismatched_passwords_rejected():
    problem = auth.password_problem("a-long-enough-passphrase", "something-else")
    assert problem == "Passwords don't match."


def test_short_password_rejected():
    short = "x" * (auth.MIN_PASSWORD_LENGTH - 1)
    assert auth.password_problem(short, short) is not None


def test_minimum_length_password_accepted():
    ok = "x" * auth.MIN_PASSWORD_LENGTH
    assert auth.password_problem(ok, ok) is None


def test_passphrase_with_spaces_accepted():
    # Length is the only rule — no composition requirements.
    phrase = "correct horse battery staple"
    assert auth.password_problem(phrase, phrase) is None


def test_mismatch_is_reported_before_length():
    # Otherwise a user fixing a typo gets told about length instead.
    assert auth.password_problem("short", "other") == "Passwords don't match."


# ── Link building ────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, base="http://testserver/"):
        self.base_url = base


def test_link_prefers_configured_public_base(monkeypatch):
    monkeypatch.setattr(auth.settings, "public_base_url", "https://piclstats.fly.dev")
    assert auth.build_link(_FakeRequest(), "/invite/abc") == "https://piclstats.fly.dev/invite/abc"


def test_link_tolerates_trailing_slash_in_config(monkeypatch):
    monkeypatch.setattr(auth.settings, "public_base_url", "https://piclstats.fly.dev/")
    assert auth.build_link(_FakeRequest(), "/invite/abc") == "https://piclstats.fly.dev/invite/abc"


def test_link_falls_back_to_request_base(monkeypatch):
    monkeypatch.setattr(auth.settings, "public_base_url", "")
    assert auth.build_link(_FakeRequest(), "/reset/xyz") == "http://testserver/reset/xyz"


# ── Mail client ──────────────────────────────────────────────────────


def test_unconfigured_mail_logs_instead_of_sending(monkeypatch, caplog):
    monkeypatch.setattr(mail.settings, "resend_api_key", "")
    monkeypatch.setattr(mail.settings, "email_from", "")

    def _explode(*args, **kwargs):
        raise AssertionError("must not call the API without credentials")

    monkeypatch.setattr(mail.httpx, "post", _explode)

    with caplog.at_level(logging.WARNING):
        assert mail.send("coach@example.org", "Subject", "the body") is True
    # The link has to reach the operator somehow when email isn't wired up.
    assert "coach@example.org" in caplog.text
    assert "the body" in caplog.text


def test_is_configured_needs_both_key_and_from(monkeypatch):
    monkeypatch.setattr(mail.settings, "resend_api_key", "re_key")
    monkeypatch.setattr(mail.settings, "email_from", "")
    assert mail.is_configured() is False
    monkeypatch.setattr(mail.settings, "email_from", "PICL <no-reply@example.org>")
    assert mail.is_configured() is True


def _configure(monkeypatch):
    monkeypatch.setattr(mail.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(mail.settings, "email_from", "PICL <no-reply@example.org>")


class _Response:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


def test_send_posts_the_documented_resend_payload(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _Response()

    monkeypatch.setattr(mail.httpx, "post", _post)
    assert mail.send("coach@example.org", "Subj", "body", "<p>body</p>") is True

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test_key"
    assert captured["json"] == {
        "from": "PICL <no-reply@example.org>",
        "to": "coach@example.org",
        "subject": "Subj",
        "text": "body",
        "html": "<p>body</p>",
    }


def test_send_reports_failure_on_api_error(monkeypatch, caplog):
    _configure(monkeypatch)
    monkeypatch.setattr(mail.httpx, "post", lambda *a, **k: _Response(422, "domain not verified"))
    with caplog.at_level(logging.ERROR):
        assert mail.send("coach@example.org", "S", "b") is False
    assert "domain not verified" in caplog.text


def test_send_never_raises_on_network_failure(monkeypatch):
    _configure(monkeypatch)

    def _boom(*args, **kwargs):
        raise mail.httpx.ConnectError("no route to host")

    monkeypatch.setattr(mail.httpx, "post", _boom)
    # An email outage must not 500 the request that triggered it — the admin
    # still has the link on screen.
    assert mail.send("coach@example.org", "S", "b") is False


@pytest.mark.parametrize(
    "sender,expected_subject",
    [("Ariana", "Your PICL Stats invitation"), (None, "Your PICL Stats invitation")],
)
def test_invite_email_contains_the_link(monkeypatch, sender, expected_subject):
    _configure(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        mail.httpx,
        "post",
        lambda url, json=None, **k: (captured.update(json), _Response())[1],
    )
    link = "https://piclstats.fly.dev/invite/tok123"
    assert mail.send_invite("coach@example.org", link, sender, 7) is True
    assert captured["subject"] == expected_subject
    assert link in captured["text"]
    assert link in captured["html"]
    assert "7 days" in captured["text"]


def test_reset_email_contains_the_link(monkeypatch):
    _configure(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        mail.httpx,
        "post",
        lambda url, json=None, **k: (captured.update(json), _Response())[1],
    )
    link = "https://piclstats.fly.dev/reset/tok456"
    assert mail.send_password_reset("coach@example.org", link, 1) is True
    assert link in captured["text"]
    assert "1 hour" in captured["text"] and "1 hours" not in captured["text"]


def test_email_html_escapes_injected_markup(monkeypatch):
    # Nothing user-controlled reaches these today, but the inviter name is one
    # refactor away from being free text.
    _configure(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        mail.httpx,
        "post",
        lambda url, json=None, **k: (captured.update(json), _Response())[1],
    )
    mail.send_invite("coach@example.org", "https://x/invite/t", "<script>alert(1)</script>", 7)
    assert "<script>" not in captured["html"]
    assert "&lt;script&gt;" in captured["html"]


# ── Reset throttle ───────────────────────────────────────────────────


def test_reset_throttle_is_tighter_than_login():
    # /forgot triggers an outbound email, so it's an abuse vector rather than a
    # guessing one.
    assert auth.reset_throttle.max_attempts < auth.throttle.max_attempts
