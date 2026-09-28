"""Every response carries the security headers; HSTS only over https."""

from fastapi.testclient import TestClient

from piclstats.web.app import app


def test_headers_on_a_public_page():
    client = TestClient(app)
    r = client.get("/login")
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "https://cdn.jsdelivr.net" in csp
    assert "strict-transport-security" not in r.headers  # plain http in the test client


def test_hsts_when_the_proxy_says_https():
    client = TestClient(app)
    r = client.get("/login", headers={"x-forwarded-proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
