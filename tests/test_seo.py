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


def test_chart_pages_load_chart_js_before_using_it():
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "src" / "piclstats" / "web" / "templates"
    base = (templates / "base.html").read_text()
    assert "chart.umd.min.js" not in base  # not on every page any more
    for path in templates.rglob("*.html"):
        text = path.read_text()
        if "new Chart(" in text and path.name != "_racechart_scripts.html":
            inc = text.find('{% include "_chartjs.html" %}')
            assert 0 <= inc < text.find("new Chart("), path.name


def test_responses_are_gzipped_and_static_is_cached():
    client = TestClient(app)
    r = client.get("/login", headers={"accept-encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    r = client.get("/static/og-image.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_results_page_canonical_names_the_event(monkeypatch):
    from piclstats.web import queries

    from piclstats.web import app as app_module

    monkeypatch.setattr(queries, "all_events", lambda session: [])
    monkeypatch.setattr(app_module, "published_local_events", lambda session: [], raising=False)
    import piclstats.web.timing_station as station

    monkeypatch.setattr(station, "published_local_events", lambda session: [])
    r = TestClient(app).get("/results")
    assert r.status_code == 200
    assert '<link rel="canonical" href="https://piclstats.com/results">' in r.text


def test_privacy_page_is_public_and_linked_from_the_footer():
    r = TestClient(app).get("/privacy")
    assert r.status_code == 200
    assert "support@piclstats.com" in r.text and "hide the rider" in r.text
    assert "Sentry" in r.text  # every service that receives data is named
    assert 'href="/privacy"' in r.text  # the footer link, on every page
    assert 'name="robots"' not in r.text


def test_description_appears_only_in_meta_tags(monkeypatch):
    from piclstats.web import queries

    monkeypatch.setattr(queries, "schedule_seasons", lambda session: [])
    h = TestClient(app).get("/schedule").text
    head, body = h.split("</head>", 1)
    assert "The PICL race schedule" in head and "The PICL race schedule" not in body
    assert h.lstrip().startswith("<!DOCTYPE html>")
    assert h.index("<head>") - h.index("<html") < 120  # nothing printed between html and head


def test_healthz_is_ok_when_the_database_answers(monkeypatch):
    from piclstats.web import app as app_mod

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt):
            return None

    monkeypatch.setattr(app_mod, "get_session", lambda: _Session())
    # Fly calls the private address; that must not 301 to the public host.
    monkeypatch.setattr(app_mod.settings, "public_base_url", "https://piclstats.com")
    r = TestClient(app).get("/healthz", headers={"host": "172.19.0.2:8080"}, follow_redirects=False)
    assert r.status_code == 200 and r.text == "ok\n"
    assert r.headers["cache-control"] == "no-store"


def test_healthz_is_503_when_the_database_does_not(monkeypatch):
    from piclstats.web import app as app_mod

    def _broken():
        raise RuntimeError("server closed the connection unexpectedly")

    monkeypatch.setattr(app_mod, "get_session", _broken)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 503


def test_terms_page_is_public_linked_and_forbids_scraping():
    r = TestClient(app).get("/terms")
    assert r.status_code == 200
    assert "Automated collection" in r.text and "machine-learning or AI models" in r.text
    assert 'href="/robots.txt"' in r.text and 'href="/privacy"' in r.text
    assert 'href="/terms"' in r.text  # the footer link, on every page
    assert 'name="robots"' not in r.text
