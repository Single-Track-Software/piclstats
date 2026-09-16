"""Canonical-host redirect decisions (pure — no app or DB)."""

from piclstats.web.canonical import canonical_host, redirect_target

BASE = "https://piclstats.com"


def test_unset_base_url_never_redirects():
    assert redirect_target("GET", "piclstats.fly.dev", "/", "", "") is None


def test_canonical_host_serves():
    assert redirect_target("GET", "piclstats.com", "/riders", "q=x", BASE) is None
    assert redirect_target("GET", "PICLSTATS.COM:443", "/", "", BASE) is None


def test_other_hosts_redirect_with_path_and_query():
    assert (
        redirect_target("GET", "www.piclstats.com", "/rider/4766", "season=2026", BASE)
        == "https://piclstats.com/rider/4766?season=2026"
    )
    assert redirect_target("HEAD", "piclstats.fly.dev", "/", "", BASE) == "https://piclstats.com/"


def test_unsafe_methods_are_left_alone():
    assert redirect_target("POST", "www.piclstats.com", "/login", "", BASE) is None


def test_missing_host_header_serves():
    assert redirect_target("GET", "", "/", "", BASE) is None


def test_canonical_host_parsing():
    assert canonical_host("https://piclstats.com/") == "piclstats.com"
    assert canonical_host("http://localhost:8000") == "localhost"
    assert canonical_host("") == ""
