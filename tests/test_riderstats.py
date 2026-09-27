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


# ── Career highlights ───────────────────────────────────────────────────

from piclstats.web.riderstats import career_highlights  # noqa: E402


def _career(season, order, place, field=20, best_lap=600.0, laps=2, course_id=1, **kw):
    return race(
        season,
        order,
        place=place,
        field_size=field,
        percentile=round((1 - place / field) * 100, 1) if place else None,
        best_lap_secs=best_lap,
        laps_ridden=laps,
        loop_distance=4.0,
        course_id=course_id,
        course=f"Course {course_id}",
        event_name=f"Race {season}-{order}",
        **kw,
    )


def test_highlights_counts_and_bests_carry_their_race():
    rows = [
        _career(2025, 1, 8, best_lap=620.0),
        _career(2025, 2, 3, best_lap=590.0, course_id=2),
        _career(2025, 3, None, status="DNF", laps=1, best_lap=700.0),
        _career(2026, 1, 1, field=30, best_lap=575.0, laps=3),
        _career(2026, 2, 1, field=12, best_lap=580.0),
    ]
    h = career_highlights(rows)
    assert (h["races"], h["seasons"], h["wins"], h["podiums"], h["top10"]) == (4, 2, 2, 3, 4)
    assert h["laps"] == 10 and h["miles"] == 40  # the DNF's lap still counts
    assert h["best_finish"]["place"] == 1 and h["best_finish"]["field_size"] == 30
    assert h["best_finish"]["event_name"] == "Race 2026-1"
    assert h["fastest_lap"]["text"] == "9:35" and h["fastest_lap"]["course"] == "Course 1"
    assert h["course_bests"][2]["text"] == "9:50"
    assert h["best_percentile"]["value"] == round((1 - 1 / 30) * 100, 1)
    assert [m["label"] for m in h["milestones"]] == ["First podium", "First win"]
    assert h["milestones"][0]["when"] == "Race 2025-2 (2025)"


def test_highlights_skip_excluded_and_non_scoring_rows():
    rows = [
        _career(2025, 1, 1, dq_status="excluded", best_lap=100.0),
        _career(2025, 2, 1, event_type="rally", best_lap=200.0),
        _career(2025, 3, 5, best_lap=600.0),
    ]
    h = career_highlights(rows)
    assert h["wins"] == 0 and h["races"] == 1
    # A rally's lap columns hold segment times, an excluded race's are not trusted.
    assert h["fastest_lap"]["value"] == 600.0 and h["laps"] == 2
    assert career_highlights([]) is None


def test_race_and_lap_milestones_name_the_race_they_fell_on():
    rows = [_career(2025, i, 5, laps=5) for i in range(1, 12)]
    h = career_highlights(rows)
    labels = {m["label"]: m["when"] for m in h["milestones"]}
    assert labels["10th race"] == "Race 2025-10 (2025)"
    assert labels["25 laps"] == "Race 2025-5 (2025)" and labels["50 laps"] == "Race 2025-10 (2025)"
    assert "25th race" not in labels


def test_first_podium_that_is_the_first_win_is_one_line():
    h = career_highlights([_career(2025, 1, 1), _career(2025, 2, 2)])
    assert [m["label"] for m in h["milestones"]] == ["First win"]


def test_season_summary_counts_laps_ridden():
    rows = [race(2025, 1, laps_ridden=2), race(2025, 2, laps_ridden=1, status="DNF", place=None)]
    (s,) = season_summary(rows)
    assert s["laps"] == 3
