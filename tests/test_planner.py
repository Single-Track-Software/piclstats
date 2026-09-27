"""Race duration planner (pure): finish spread, cutoff, window status, baselines."""

from __future__ import annotations

import math

from piclstats.web.planner import plan_course, plan_division, typical_baseline
from piclstats.web.ratings import Rating


def _r(mean, sd=0.065):
    return Rating(mean=mean, sd=sd, races=3, stale=False)


# A day effect of log(600): a rider rated 0.0 laps in 10 minutes.
BASE = math.log(600)


def test_plan_division_spread_and_ok_status():
    riders = [_r(-0.1), _r(0.0), _r(0.1)]
    d = plan_division(riders, 2, BASE, window_mins=45, cutoff_mins=23)
    assert d["riders"] == 3 and d["laps"] == 2
    assert d["winner_mins"] == round(2 * 600 * math.exp(-0.1) / 60, 1)
    assert d["median_mins"] == 20.0
    # Last rider on a slow day: exp(0.1 + 0.065) * 600 * 2 / 60
    assert d["last_mins"] == round(2 * 600 * math.exp(0.165) / 60, 1)
    assert d["pulled"] == 0 and d["status"] == "ok" and d["headroom_mins"] > 0


def test_window_status_tight_and_over():
    riders = [_r(0.0)]
    # 4 laps of 10 min = 40; slow day ~42.7: tight against 45, over against 40.
    assert plan_division(riders, 4, BASE, 45, None)["status"] == "tight"
    assert plan_division(riders, 4, BASE, 40, None)["status"] == "over"
    assert plan_division(riders, 4, BASE, None, None)["status"] is None


def test_cutoff_pulls_slow_riders_and_last_is_then_a_finisher():
    fast, slow = _r(0.0), _r(0.7)  # 10-min laps vs ~20-min laps
    d = plan_division([fast, slow], 3, BASE, window_mins=60, cutoff_mins=35)
    # Slow rider is at ~40 min after two laps, past the 35-min cutoff: pulled.
    assert d["pulled"] == 1
    assert d["last_mins"] == round(3 * 600 * math.exp(0.065) / 60, 1)


def test_empty_division_or_no_laps_is_graceful():
    assert plan_division([], 2, BASE, 45, 23) == {"laps": 2, "riders": 0}
    assert plan_division([_r(0.0)], 0, BASE, 45, 23) == {"laps": 0, "riders": 1}


def test_typical_baseline_averages_the_loop_across_courses():
    baselines = {(1, "HS", "Male"): 6.0, (2, "HS", "Male"): 7.0, (3, "MS", "Male"): 9.0}
    assert typical_baseline(baselines, "HS", "Male") == 6.5
    assert typical_baseline(baselines, "HS", "Female") is None


def _row(event_id, rider_id, lap_secs, division="JV2", course_id=5, season=2026,
         conf="Western", gender="Male", loop_type="HS"):  # fmt: skip
    return {
        "event_id": event_id,
        "season": season,
        "event_order": event_id,
        "rider_id": rider_id,
        "division": division,
        "gender": gender,
        "loop_type": loop_type,
        "laps": 2,
        "lap_secs": lap_secs,
        "conference": conf,
        "category_order": 1,
        "place": 1,
        "team": "T",
        "course_id": course_id,
    }


def _field(event_id, course_id=5, base=600.0, conf="Western", division="JV2"):
    return [
        _row(
            event_id,
            100 * event_id + i,
            base + 10 * i,
            course_id=course_id,
            conf=conf,
            division=division,
        )  # fmt: skip
        for i in range(10)
    ]


PROFILES = [
    {
        "division": "JV2",
        "gender": "Male",
        "loop_type": "HS",
        "lap_count": 2,
        "max_duration_mins": 75,
        "cutoff_mins": 38,
    },  # fmt: skip
    {
        "division": "JV1",
        "gender": "Male",
        "loop_type": "HS",
        "lap_count": 3,
        "max_duration_mins": 75,
        "cutoff_mins": 50,
    },  # fmt: skip
]


def test_plan_course_uses_the_course_baseline_and_conference_draw():
    # Same riders raced course 5 (10-min laps) and course 7 (13-min laps).
    rows = _field(1, course_id=5) + [
        {**r, "event_id": 2, "event_order": 2, "lap_secs": r["lap_secs"] * 1.3, "course_id": 7}
        for r in _field(1, course_id=5)
    ]
    rows += _field(3, course_id=5, conf="Central")  # ten Central riders too
    by_loop = {("Male", "HS"): rows}
    state = plan_course(by_loop, 2026, 5, None, PROFILES)
    western = plan_course(by_loop, 2026, 5, "Western", PROFILES)
    slow_course = plan_course(by_loop, 2026, 7, None, PROFILES)
    (jv2,) = [d for d in state["divisions"] if d["division"] == "JV2"]
    assert jv2["riders"] == 20 and jv2["laps"] == 2 and jv2["baseline_source"] == "course"
    assert abs(jv2["lap_mins"] - 10.0) < 1.0
    (w,) = [d for d in western["divisions"] if d["division"] == "JV2"]
    assert w["riders"] == 10
    (slow,) = [d for d in slow_course["divisions"] if d["division"] == "JV2"]
    assert slow["lap_mins"] > jv2["lap_mins"] * 1.2
    assert state["conferences"] == ["Central", "Western"]
    assert not state["borrowed_baseline"]


def test_plan_course_borrows_a_typical_baseline_at_a_new_course_and_takes_overrides():
    by_loop = {("Male", "HS"): _field(1, course_id=5)}
    plan = plan_course(by_loop, 2026, 99, None, PROFILES, overrides={("JV2", "Male"): 4})
    (jv2,) = plan["divisions"]
    assert plan["borrowed_baseline"] and jv2["baseline_source"] == "typical"
    assert jv2["laps"] == 4 and jv2["profile_laps"] == 2
    assert jv2["status"] == "ok" and jv2["winner_mins"] > 35


def test_divisions_without_a_profile_are_skipped():
    by_loop = {("Male", "HS"): _field(1, division="Varsity")}
    assert plan_course(by_loop, 2026, 5, None, PROFILES)["divisions"] == []
