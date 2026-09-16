"""Season summaries from race rows (pure)."""

from piclstats.web.riderstats import season_summary


def race(season, order, **kw):
    base = dict(
        season=season,
        event_order=order,
        event_type="points",
        place=3,
        points=521,
        status="OK",
        division="JV3",
        percentile=77.0,
        pct_behind=4.2,
        min_per_mile=6.1,
        dq_status="ok",
    )
    base.update(kw)
    return base


def test_averages_placed_clean_scoring_races_only():
    rows = [
        race(2025, 1, percentile=80.0, pct_behind=2.0, min_per_mile=5.0),
        race(2025, 2, percentile=60.0, pct_behind=6.0, min_per_mile=6.0),
        race(
            2025, 3, place=None, status="DNF", percentile=None, pct_behind=None, min_per_mile=None
        ),
        race(2025, 4, dq_status="excluded", percentile=None),
        race(2025, 5, event_type="rally", percentile=99.0),
    ]
    (s,) = season_summary(rows)
    assert (s["races"], s["dnfs"]) == (2, 1)
    assert s["avg_percentile"] == 70.0
    assert (s["avg_pct_behind"], s["best_pct_behind"]) == (4.0, 2.0)
    assert (s["avg_pace"], s["best_pace"]) == (5.5, 5.0)
    assert s["total_points"] == 1042 and s["best_place"] == 3


def test_division_is_the_last_of_the_season_and_flags_a_move():
    rows = [race(2024, 1, division="JV3"), race(2024, 2, division="JV2")]
    (s,) = season_summary(rows)
    assert s["primary_division"] == "JV2" and s["division_changed"]


def test_seasons_sorted_and_empty_metrics_are_none():
    rows = [
        race(2026, 1, percentile=None, pct_behind=None, min_per_mile=None, points=None),
        race(2025, 1),
    ]
    a, b = season_summary(rows)
    assert (a["season"], b["season"]) == (2025, 2026)
    assert b["avg_percentile"] is None and b["total_points"] is None
