"""Usage log helpers (pure)."""

from datetime import date

from piclstats.web.usage import classify, is_bot, kept_query, referrer_host, should_log, visitor_id


def test_routes_and_entities():
    assert classify("/") == ("home", None)
    assert classify("/rider/102") == ("rider", "102")
    assert classify("/rider/102/forecast") == ("forecast", "102")
    assert classify("/team/Twin Valley School") == ("team", "Twin Valley School")
    assert classify("/team/Pa Independent/Delco") == ("team", "Pa Independent/Delco")
    assert classify("/staging.csv") == ("staging", None)
    assert classify("/admin/dq") == ("admin", None)
    assert classify("/whatever") == ("other", None)


def test_visitor_is_stable_within_a_day_and_changes_daily():
    a = visitor_id("1.2.3.4", "Mozilla/5.0", "secret", date(2026, 9, 16))
    b = visitor_id("1.2.3.4", "Mozilla/5.0", "secret", date(2026, 9, 16))
    c = visitor_id("1.2.3.4", "Mozilla/5.0", "secret", date(2026, 9, 17))
    d = visitor_id("1.2.3.5", "Mozilla/5.0", "secret", date(2026, 9, 16))
    assert a == b and a != c and a != d and len(a) == 16


def test_query_keeps_only_allowlisted_params():
    assert kept_query("season=2026&view=teams&token=abc&next=%2Fx") == "season=2026&view=teams"
    assert kept_query("token=abc") is None


def test_referrer_host_drops_own_host_and_paths():
    assert (
        referrer_host("https://www.facebook.com/groups/123", "piclstats.com") == "www.facebook.com"
    )
    assert referrer_host("https://piclstats.com/leaderboard", "piclstats.com") is None
    assert referrer_host(None, "piclstats.com") is None


def test_bots_and_skips():
    assert is_bot("curl/8.4") and is_bot(None) and not is_bot("Mozilla/5.0 (iPhone)")
    assert should_log("GET", "/rider/1", 200)
    assert not should_log("POST", "/login", 303)
    assert not should_log("GET", "/static/app.css", 200)
    assert not should_log("GET", "/rider/1", 500)
