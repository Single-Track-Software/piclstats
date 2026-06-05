"""Unit tests for the staging / speed-rating aggregation (pure functions)."""

from __future__ import annotations

import math

from piclstats.web.staging import build_grid, build_speed_rating, percentile_faster


def _grow(cid, name, division, per_event):
    """One row per (rider, event); per_event maps event_id -> z."""
    return [
        {"canonical_id": cid, "name": name, "team": "T", "division": division,
         "event_id": eid, "event_name": f"Race {eid}", "event_order": eid,
         "z_pace": z, "z_lap": z}
        for eid, z in per_event.items()
    ]


def _row(season, order, z_pace=None, z_lap=None, age="HS", gender="Male", field=20):
    return {
        "event_name": f"Race {order}",
        "season": season,
        "event_order": order,
        "age_group": age,
        "division": "Varsity",
        "gender": gender,
        "z_pace": z_pace,
        "z_lap": z_lap,
        "lap_field": field,
        "pace_field": field,
    }


def test_percentile_faster_is_higher_for_negative_z():
    # z = 0 is the median (50th); a fast (negative) z ranks higher.
    assert percentile_faster(0.0) == 50.0
    assert percentile_faster(-1.5) > 90
    assert percentile_faster(1.5) < 10


def test_average_and_best_of_rollup():
    rows = [
        _row(2025, 1, z_pace=-0.5),
        _row(2025, 2, z_pace=-1.5),
        _row(2025, 3, z_pace=-1.0),
    ]
    sr = build_speed_rating(rows)
    pace = sr["pace"]
    assert pace.events_used == 3
    assert math.isclose(pace.avg_z, -1.0, abs_tol=1e-9)   # mean of -.5,-1.5,-1
    assert pace.best_z == -1.5                            # most negative = fastest
    assert pace.latest_z == -1.0                          # last event
    assert pace.rating == 1.0                             # -avg_z, higher = faster
    assert pace.percentile > 50                           # faster than average


def test_both_metrics_summarized_independently():
    rows = [
        _row(2025, 1, z_pace=-1.0, z_lap=-0.8),
        _row(2025, 2, z_pace=-2.0, z_lap=None),  # missing lap z that event
    ]
    sr = build_speed_rating(rows)
    assert sr["pace"].events_used == 2
    assert sr["lap"].events_used == 1               # only one event had a lap z
    assert sr["lap"].best_z == -0.8
    assert sr["age_group"] == "HS"
    assert sr["age_group_label"] == "High School"
    assert sr["has_data"] is True


def test_no_timed_races_is_graceful():
    rows = [_row(2025, 1, z_pace=None, z_lap=None)]
    sr = build_speed_rating(rows)
    assert sr["has_data"] is False
    assert sr["pace"].avg_z is None
    assert sr["pace"].events_used == 0
    assert "Not enough" in sr["pace"].label


def test_low_confidence_flagged_under_three_events():
    sr = build_speed_rating([_row(2025, 1, z_pace=-1.0), _row(2025, 2, z_pace=-1.0)])
    assert "low confidence" in sr["pace"].label


# ── staging grid ────────────────────────────────────────────────────────

def _category_rows():
    rows = []
    rows += _grow(1, "Fast Kid", "MS Advanced", {1: -1.8, 2: -2.0})
    rows += _grow(2, "Mid Kid", "7th Grade", {1: -0.2, 2: 0.1})
    rows += _grow(3, "Slow Kid", "6th Grade", {1: 1.0, 2: 0.8})
    return rows


def test_grid_ranks_fastest_first_and_pivots_events():
    grid = build_grid(_category_rows(), sort="best")
    assert [r["name"] for r in grid["riders"]] == ["Fast Kid", "Mid Kid", "Slow Kid"]
    assert [r["rank"] for r in grid["riders"]] == [1, 2, 3]
    assert len(grid["events"]) == 2
    fast = grid["riders"][0]
    assert fast["best_z"] == -2.0           # most negative across events
    assert fast["per_event"][1] == -1.8 and fast["per_event"][2] == -2.0
    assert grid["divisions"] == ["6th Grade", "7th Grade", "MS Advanced"]


def test_grid_waves_split_by_size():
    rows = []
    for i in range(1, 6):
        rows += _grow(i, f"Kid {i}", "7th Grade", {1: float(i)})  # z = i, ascending
    grid = build_grid(rows, sort="best", wave_size=2)
    waves = [r["wave"] for r in grid["riders"]]
    assert waves == [1, 1, 2, 2, 3]


def test_grid_division_filter_reranks_within_division():
    grid = build_grid(_category_rows(), sort="best", division="7th Grade")
    assert [r["name"] for r in grid["riders"]] == ["Mid Kid"]
    assert grid["riders"][0]["rank"] == 1
    # full division list still offered for the dropdown
    assert "MS Advanced" in grid["divisions"]


def test_grid_avg_sort_differs_from_best():
    # A kid with one great race but poor average vs a steady kid.
    rows = _grow(1, "Spiky", "7th Grade", {1: -3.0, 2: 1.0})   # best -3.0, avg -1.0
    rows += _grow(2, "Steady", "7th Grade", {1: -1.4, 2: -1.4})  # best -1.4, avg -1.4
    by_best = build_grid(rows, sort="best")
    by_avg = build_grid(rows, sort="avg")
    assert by_best["riders"][0]["name"] == "Spiky"
    assert by_avg["riders"][0]["name"] == "Steady"
