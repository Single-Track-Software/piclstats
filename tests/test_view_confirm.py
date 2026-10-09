"""Browser-confirmed page views: the id in the page, and the /v endpoint's checks."""

import re

from fastapi.testclient import TestClient

from piclstats.web import usage
from piclstats.web import app as app_mod
from piclstats.web.app import app

HOST = "testserver"
SAME = {"origin": f"http://{HOST}"}


def _captured(monkeypatch):
    calls = []
    monkeypatch.setattr(
        usage, "confirm", lambda vid, visitors, via: calls.append((vid, visitors, via))
    )
    monkeypatch.setattr(usage, "record", lambda row: None)
    app_mod._confirm_hits.clear()
    return calls


def test_html_pages_embed_a_fresh_view_id_and_the_script():
    c = TestClient(app)
    a, b = c.get("/terms").text, c.get("/terms").text
    ids = [re.search(r'const id = "([A-Za-z0-9_-]+)";', h).group(1) for h in (a, b)]
    assert all(usage.valid_view_id(i) for i in ids) and ids[0] != ids[1]
    assert "navigator.webdriver" in a and 'sendBeacon("/v"' in a


def test_json_responses_get_no_script():
    assert "sendBeacon" not in TestClient(app).get("/robots.txt").text


def test_a_valid_confirmation_is_queued_with_todays_and_yesterdays_visitor(monkeypatch):
    calls = _captured(monkeypatch)
    vid = usage.new_view_id()
    r = TestClient(app).post("/v", content=f'{{"id": "{vid}", "via": "dwell"}}', headers=SAME)
    assert r.status_code == 204 and r.content == b""
    assert len(calls) == 1 and calls[0][0] == vid and calls[0][2] == "dwell"
    assert len(calls[0][1]) == 2 and calls[0][1][0] != calls[0][1][1]


def test_rejected_quietly_without_origin_bad_id_or_bad_via(monkeypatch):
    calls = _captured(monkeypatch)
    c = TestClient(app)
    vid = usage.new_view_id()
    for body, headers in [
        (f'{{"id": "{vid}", "via": "dwell"}}', {}),  # no Origin
        (f'{{"id": "{vid}", "via": "dwell"}}', {"origin": "https://evil.example"}),
        ('{"id": "short", "via": "dwell"}', SAME),
        (f'{{"id": "{vid}", "via": "robot"}}', SAME),
        ("not json", SAME),
    ]:
        r = c.post("/v", content=body, headers=headers)
        assert r.status_code == 204 and r.content == b""
    assert calls == []


def test_per_ip_rate_limit(monkeypatch):
    calls = _captured(monkeypatch)
    c = TestClient(app)
    for _ in range(app_mod._CONFIRM_LIMIT + 5):
        c.post(
            "/v", content=f'{{"id": "{usage.new_view_id()}", "via": "interaction"}}', headers=SAME
        )
    assert len(calls) == app_mod._CONFIRM_LIMIT


def test_view_ids_are_validated():
    assert usage.valid_view_id(usage.new_view_id())
    assert not usage.valid_view_id("x" * 10)
    assert not usage.valid_view_id("bad id with spaces and more chars")
    assert not usage.valid_view_id(None)
