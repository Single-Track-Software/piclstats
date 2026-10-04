"""Sentry stays off without a DSN and never sends emails or URL tokens."""

from piclstats.web import monitoring


def test_off_without_a_dsn(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "sentry_dsn", "")
    assert monitoring.init() is False


def test_scrubs_emails_and_token_urls_everywhere_in_an_event():
    event = {
        "request": {"url": "https://piclstats.com/invite/AbC123xyz?next=/admin"},
        "transaction": "/reset/s3cr3t-token",
        "exception": {"values": [{"value": "no user coach.smith+picl@school.k12.pa.us"}]},
        "breadcrumbs": {
            "values": [{"message": "GET /timing/s/ABCDEFGH", "data": {"to": ["a@b.org"]}}]
        },
        "tags": ("rider", 5514),
    }
    out = monitoring._before_send(event, None)
    assert out["request"]["url"] == "https://piclstats.com/invite/[token]?next=/admin"
    assert out["transaction"] == "/reset/[token]"
    assert out["exception"]["values"][0]["value"] == "no user [email]"
    assert out["breadcrumbs"]["values"][0]["message"] == "GET /timing/s/[token]"
    assert out["breadcrumbs"]["values"][0]["data"]["to"] == ["[email]"]
    assert out["tags"] == ("rider", 5514)


def test_leaves_ordinary_pages_alone():
    assert monitoring.scrub("/rider/5514") == "/rider/5514"
    assert monitoring.scrub("/team/Twin Valley School") == "/team/Twin Valley School"
    assert monitoring.scrub("/timing/") == "/timing/"


def test_init_passes_the_privacy_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(monitoring.settings, "sentry_dsn", "https://k@o1.ingest.sentry.io/1")
    monkeypatch.setattr(monitoring.sentry_sdk, "init", lambda **kw: seen.update(kw))
    assert monitoring.init() is True
    assert seen["send_default_pii"] is False
    assert seen["max_request_body_size"] == "never"
    assert seen["include_local_variables"] is False
    assert seen["before_send"] is monitoring._before_send
    assert "enable_logs" not in seen
