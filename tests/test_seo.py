"""Head tags, robots and sitemap."""

from fastapi.testclient import TestClient

from piclstats.web.app import app


def test_public_page_has_description_canonical_and_og(monkeypatch):
    # CI has no database: an empty season list short-circuits every query on the page.
    from piclstats.web import queries

    monkeypatch.setattr(queries, "schedule_seasons", lambda session: [])
    r = TestClient(app).get("/schedule")
    h = r.text
    assert '<meta name="description" content="The PICL race schedule' in h
    assert '<link rel="canonical" href="https://piclstats.com/schedule">' in h
    assert '<meta property="og:title" content="Schedule' in h
    assert 'name="robots"' not in h


def test_private_pages_are_noindex_without_canonical():
    h = TestClient(app).get("/login").text
    assert '<meta name="robots" content="noindex, nofollow">' in h
    assert 'rel="canonical"' not in h and "og:title" not in h


def test_robots_txt_blocks_accounts_admin_and_stations():
    r = TestClient(app).get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    for p in ("/admin", "/staging", "/login", "/reset/", "/invite/", "/timing/"):
        assert f"Disallow: {p}\n" in body
    assert "Disallow: /rider" not in body and "Disallow: /results" not in body
    assert "Sitemap: https://piclstats.com/sitemap.xml" in body


def test_favicon_ico_points_at_the_svg():
    r = TestClient(app).get("/favicon.ico", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/static/favicon.svg"


def test_canonical_percent_encodes_the_path():
    from urllib.parse import quote

    from jinja2 import Environment

    # Jinja's urlencode keeps "/" and encodes the space, which is what a canonical needs.
    assert Environment().from_string("{{ p | urlencode }}").render(p="/team/Pgh North") == quote(
        "/team/Pgh North"
    )
