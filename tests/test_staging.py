"""Unit tests for the staging / speed-rating aggregation (pure functions)."""

from __future__ import annotations

import math

from piclstats.web.staging import (
    SHEET_CATEGORIES,
    build_grid,
    build_sheet,
    build_speed_rating,
    group_color,
    percentile_faster,
)


def _grow(cid, name, division, per_event, conf=None, group=None):
    """One row per (rider, event); per_event maps event_id -> z."""
    return [
        {
            "canonical_id": cid,
            "name": name,
            "team": "T",
            "division": division,
            "conference": conf,
            "conference_group": group,
            "event_id": eid,
            "event_name": f"Race {eid}",
            "event_order": eid,
            "z_pace": z,
            "z_lap": z,
        }
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
    assert math.isclose(pace.avg_z, -1.0, abs_tol=1e-9)  # mean of -.5,-1.5,-1
    assert pace.best_z == -1.5  # most negative = fastest
    assert pace.latest_z == -1.0  # last event
    assert pace.rating == 1.0  # -avg_z, higher = faster
    assert pace.percentile > 50  # faster than average


def test_both_metrics_summarized_independently():
    rows = [
        _row(2025, 1, z_pace=-1.0, z_lap=-0.8),
        _row(2025, 2, z_pace=-2.0, z_lap=None),  # missing lap z that event
    ]
    sr = build_speed_rating(rows)
    assert sr["pace"].events_used == 2
    assert sr["lap"].events_used == 1  # only one event had a lap z
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
    assert [r["rank"] for r in grid["riders"]] == [1, 1, 1]  # position within the division
    assert len(grid["events"]) == 2
    fast = grid["riders"][0]
    assert fast["best_z"] == -2.0  # most negative across events
    assert fast["per_event"][1] == -1.8 and fast["per_event"][2] == -2.0
    assert grid["divisions"] == ["MS Advanced", "7th Grade", "6th Grade"]  # ladder order


def test_grid_groups_split_by_size():
    rows = []
    for i in range(1, 6):
        rows += _grow(i, f"Kid {i}", "7th Grade", {1: float(i)})  # z = i, ascending
    grid = build_grid(rows, sort="best", group_size=2)
    groups = [r["group"] for r in grid["riders"]]
    assert groups == [1, 1, 2, 2, 3]


def test_grid_division_filter_reranks_within_division():
    grid = build_grid(_category_rows(), sort="best", division="7th Grade")
    assert [r["name"] for r in grid["riders"]] == ["Mid Kid"]
    assert grid["riders"][0]["rank"] == 1
    # full division list still offered for the dropdown
    assert "MS Advanced" in grid["divisions"]


def test_grid_avg_sort_differs_from_best():
    # A kid with one great race but poor average vs a steady kid.
    rows = _grow(1, "Spiky", "7th Grade", {1: -3.0, 2: 1.0})  # best -3.0, avg -1.0
    rows += _grow(2, "Steady", "7th Grade", {1: -1.4, 2: -1.4})  # best -1.4, avg -1.4
    by_best = build_grid(rows, sort="best")
    by_avg = build_grid(rows, sort="avg")
    assert by_best["riders"][0]["name"] == "Spiky"
    assert by_avg["riders"][0]["name"] == "Steady"


def _conf_rows():
    rows = []
    rows += _grow(1, "Blue Fast", "7th Grade", {1: -2.0}, conf="Eastern Blue", group="Eastern")
    rows += _grow(2, "Gold Fast", "7th Grade", {1: -1.5}, conf="Eastern Gold", group="Eastern")
    rows += _grow(3, "Central Kid", "7th Grade", {1: -1.0}, conf="Central", group="Central")
    return rows


def test_grid_conference_dropdown_groups_only_when_multi():
    grid = build_grid(_conf_rows())
    assert grid["conferences"] == ["Central", "Eastern Blue", "Eastern Gold"]
    assert "Eastern" in grid["conference_groups"]  # spans Blue + Gold
    assert "Central" not in grid["conference_groups"]  # single conference


def test_grid_conference_filter_specific_vs_combined():
    # Specific conference → only that conference's kids.
    blue = build_grid(_conf_rows(), conference="Eastern Blue")
    assert [r["name"] for r in blue["riders"]] == ["Blue Fast"]
    # Combined group → Blue + Gold staged together, re-ranked.
    eastern = build_grid(_conf_rows(), conference="Eastern")
    assert [r["name"] for r in eastern["riders"]] == ["Blue Fast", "Gold Fast"]
    assert [r["rank"] for r in eastern["riders"]] == [1, 2]


def test_grid_carries_conference_on_rider():
    by_name = {r["name"]: r for r in build_grid(_conf_rows())["riders"]}
    assert by_name["Blue Fast"]["conference"] == "Eastern Blue"
    assert by_name["Blue Fast"]["conference_group"] == "Eastern"


# ── Division order, wave formats, groups, rows ───────────────────────────


def _ms_field():
    """3 MS Advanced, 5 8th Grade, 4 7th Grade; z rises with the rider id."""
    rows = []
    for i, division in enumerate(["7th Grade"] * 4 + ["MS Advanced"] * 3 + ["8th Grade"] * 5):
        rows += _grow(i, f"Kid {i}", division, {1: -2.0 + 0.1 * i})
    return rows


def _hs_field(n=3):
    rows = []
    i = 0
    for division in ("Varsity", "JV1", "JV2", "JV3"):
        for _ in range(n):
            rows += _grow(i, f"Kid {i}", division, {1: -2.0 + 0.1 * i})
            i += 1
    return rows


def _by_division(grid, key):
    out: dict = {}
    for r in grid["riders"]:
        out.setdefault(r["division"], []).append(r[key])
    return out


def test_grid_is_division_first_even_when_a_lower_division_is_faster():
    grid = build_grid(_ms_field())  # the 7th graders have the best z of all
    assert list(_by_division(grid, "rank")) == ["MS Advanced", "8th Grade", "7th Grade"]
    assert _by_division(grid, "rank")["8th Grade"] == [1, 2, 3, 4, 5]


def test_separate_waves_restart_the_group_count():
    grid = build_grid(_ms_field(), group_size=2, wave_format="separate")
    assert _by_division(grid, "group") == {
        "MS Advanced": [1, 1, 2],
        "8th Grade": [1, 1, 2, 2, 3],
        "7th Grade": [1, 1, 2, 2],
    }
    assert [w["divisions"] for w in grid["waves"]] == [
        ["MS Advanced"],
        ["8th Grade"],
        ["7th Grade"],
    ]


def test_state_format_runs_groups_on_from_ms_advanced_into_8th_grade_for_boys():
    grid = build_grid(_ms_field(), group_size=4, wave_format="state", gender="Male")
    groups = _by_division(grid, "group")
    assert groups["MS Advanced"] == [1, 1, 1]  # small enough for one group
    assert groups["8th Grade"] == [2, 2, 2, 2, 3]  # its first group is group 2
    assert groups["7th Grade"] == [1, 1, 1, 1]  # its own wave
    assert [w["divisions"] for w in grid["waves"]] == [["MS Advanced", "8th Grade"], ["7th Grade"]]
    assert _by_division(grid, "wave")["8th Grade"] == [1] * 5


def test_state_format_stages_all_ms_girls_as_one_wave():
    # Penn College 2026: MS girls ran Red (MS Adv) through Purple (6th) in one wave.
    grid = build_grid(_ms_field(), group_size=4, wave_format="state", gender="Female")
    assert len(grid["waves"]) == 1
    assert _by_division(grid, "group")["7th Grade"] == [4, 4, 4, 4]


def test_conference_format_pairs_hs_boys_and_merges_everyone_else():
    boys = build_grid(_hs_field(), group_size=3, wave_format="conference", gender="Male")
    assert [w["divisions"] for w in boys["waves"]] == [["Varsity", "JV1"], ["JV2", "JV3"]]
    assert _by_division(boys, "group") == {
        "Varsity": [1, 1, 1],
        "JV1": [2, 2, 2],
        "JV2": [1, 1, 1],
        "JV3": [2, 2, 2],
    }
    girls = build_grid(_hs_field(), group_size=3, wave_format="conference", gender="Female")
    assert len(girls["waves"]) == 1
    assert _by_division(girls, "group")["JV3"] == [4, 4, 4]
    ms_boys = build_grid(_ms_field(), group_size=4, wave_format="conference", gender="Male")
    assert len(ms_boys["waves"]) == 1


def test_combined_format_numbers_groups_across_every_division():
    grid = build_grid(_ms_field(), group_size=4, wave_format="combined")
    groups = _by_division(grid, "group")
    assert (groups["MS Advanced"], groups["8th Grade"], groups["7th Grade"]) == (
        [1, 1, 1],
        [2, 2, 2, 2, 3],
        [4, 4, 4, 4],
    )
    assert len(grid["waves"]) == 1


def test_custom_format_joins_only_the_ticked_divisions():
    grid = build_grid(_ms_field(), group_size=4, wave_format="custom", custom_joins=["7th Grade"])
    assert [w["divisions"] for w in grid["waves"]] == [["MS Advanced"], ["8th Grade", "7th Grade"]]
    assert _by_division(grid, "group")["7th Grade"] == [3, 3, 3, 3]
    # The top division has nothing above it to join.
    top = build_grid(_ms_field(), wave_format="custom", custom_joins=["MS Advanced"])
    assert len(top["waves"]) == 3


def test_unknown_format_falls_back_to_conference():
    assert build_grid(_ms_field(), wave_format="bogus")["wave_format"] == "conference"


def test_groups_fill_to_size_then_overflow_without_balancing():
    # Belmont Blue 2026: JV1 had 29 riders → a full group of 28 and one rider in group 3.
    rows = []
    for i in range(29):
        rows += _grow(i, f"Kid {i}", "JV1", {1: 0.1 * i})
    grid = build_grid(rows)  # league defaults: 28 per group, rows of 7
    groups = _by_division(grid, "group")["JV1"]
    assert groups.count(1) == 28 and groups.count(2) == 1
    rows_ = _by_division(grid, "row")["JV1"]
    assert rows_[:28] == [1] * 7 + [2] * 7 + [3] * 7 + [4] * 7 and rows_[28] == 1


def test_group_colours_follow_the_league_map_and_restart_per_wave():
    grid = build_grid(_ms_field(), group_size=2, wave_format="combined")
    colours = [r["color"] for r in grid["riders"]]
    assert colours[:4] == ["Red", "Red", "Yellow", "Green"]  # 8th Grade opens group 3
    assert grid["riders"][-1]["color"] == "Purple"  # 7 groups over 12 riders
    separate = build_grid(_ms_field(), group_size=2, wave_format="separate")
    assert _by_division(separate, "color")["7th Grade"] == ["Red", "Red", "Yellow", "Yellow"]
    assert group_color(8) == "White" and group_color(9) == "Group 9" and group_color(None) == ""


def test_rows_restart_in_every_group():
    grid = build_grid(_ms_field(), group_size=3, row_size=2, wave_format="separate")
    assert _by_division(grid, "group")["8th Grade"] == [1, 1, 1, 2, 2]
    assert _by_division(grid, "row")["8th Grade"] == [1, 1, 2, 1, 1]  # group 2 starts at row 1
    assert _by_division(grid, "row")["MS Advanced"] == [1, 1, 2]
    assert set(_by_division(build_grid(_ms_field(), row_size=0), "row")["8th Grade"]) == {None}


def test_unrated_riders_go_to_the_back_of_their_division_without_a_group_or_row():
    rows = _ms_field() + _grow(99, "No Time", "MS Advanced", {1: None})
    grid = build_grid(rows, group_size=2, row_size=2, wave_format="state", gender="Male")
    adv = [r for r in grid["riders"] if r["division"] == "MS Advanced"]
    assert adv[-1]["name"] == "No Time" and adv[-1]["rank"] == 4
    assert adv[-1]["group"] is None and adv[-1]["row"] is None and adv[-1]["color"] is None
    assert _by_division(grid, "group")["8th Grade"][0] == 3  # unrated rider opened no group


def test_plate_comes_from_the_latest_race():
    rows = _grow(1, "Kid", "JV1", {1: -1.0, 2: -1.0})
    rows[0]["bib"] = "1501"
    rows[1]["bib"] = "1599"
    assert build_grid(rows)["riders"][0]["plate"] == "1599"
    rows[1]["bib"] = None  # a race with no plate recorded keeps the last known one
    assert build_grid(rows)["riders"][0]["plate"] == "1501"


# ── Whole-race sheet ─────────────────────────────────────────────────────


def test_sheet_lists_categories_in_league_order_and_skips_unrated():
    boys = build_grid(_hs_field(1) + _grow(99, "No Time", "JV3", {1: None}), gender="Male")
    girls = build_grid(_ms_field(), gender="Female")
    sheet = build_sheet([("HS", "Male", boys), ("MS", "Female", girls)])
    assert [r["category"] for r in sheet][:4] == [
        "Varsity - Male",
        "JV1 - Male",
        "JV2 - Male",
        "JV3 - Male",
    ]
    assert sheet[4]["category"] == "MS Advanced - Female"
    assert "No Time" not in [r["name"] for r in sheet]
    assert {"plate", "name", "team", "category", "wave", "group", "color", "row"} <= set(sheet[0])
    assert sheet[0]["color"] == "Red" and sheet[1]["group"] == 2  # JV1 rides with Varsity
    assert SHEET_CATEGORIES == [("HS", "Male"), ("HS", "Female"), ("MS", "Female"), ("MS", "Male")]
