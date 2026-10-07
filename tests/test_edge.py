"""Cloudflare origin lock and the trusted client IP."""

from fastapi.testclient import TestClient

from piclstats.web import edge
from piclstats.web.app import app

SECRET = "s3cret-origin-value"


def _lock(monkeypatch, value=SECRET):
    monkeypatch.setattr(edge.settings, "origin_secret", value)


def test_without_a_secret_nothing_changes(monkeypatch):
    _lock(monkeypatch, "")
    assert TestClient(app).get("/robots.txt").status_code == 200


def test_requests_that_skip_cloudflare_are_refused(monkeypatch):
    _lock(monkeypatch)
    c = TestClient(app)
    assert c.get("/robots.txt").status_code == 403
    assert c.get("/robots.txt", headers={edge.ORIGIN_HEADER: "wrong"}).status_code == 403
    assert c.get("/robots.txt", headers={edge.ORIGIN_HEADER: SECRET}).status_code == 200


def test_healthz_stays_open_for_flys_check(monkeypatch):
    _lock(monkeypatch)
    from piclstats.web import app as app_mod

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt):
            return None

    monkeypatch.setattr(app_mod, "get_session", lambda: _S())
    assert TestClient(app).get("/healthz").status_code == 200


class _Req:
    def __init__(self, headers, peer="10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": peer})()
        self.url = type("U", (), {"path": "/"})()


def test_cloudflare_ip_trusted_only_with_the_secret(monkeypatch):
    _lock(monkeypatch)
    hdrs = {"cf-connecting-ip": "203.0.113.9", "fly-client-ip": "172.70.1.1"}
    assert edge.client_ip(_Req({**hdrs, edge.ORIGIN_HEADER: SECRET})) == "203.0.113.9"
    # A caller can't pick its own throttle key by sending CF-Connecting-IP.
    assert edge.client_ip(_Req(hdrs)) == "172.70.1.1"


def test_without_cloudflare_fly_header_then_socket(monkeypatch):
    _lock(monkeypatch, "")
    assert edge.client_ip(_Req({"fly-client-ip": "198.51.100.7"})) == "198.51.100.7"
    assert edge.client_ip(_Req({})) == "10.0.0.1"
