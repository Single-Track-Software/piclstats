"""Race-day team sheet (pure): race choice, staging lines, forecasts, time estimates."""

from __future__ import annotations

import math
from datetime import date

from piclstats.web import raceday
from piclstats.web.staging import build_grid


def _race(id, conference, course_id=5, name="Race"):
    return {
        "id": id,
        "season": 2026,
        "event_date": date(2026, 10, 3),
        "name": name,
        "conference": conference,
        "course_id": course_id,
        "race_type": "race",
    }


RACES = [_race(1, "Eastern Blue"), _race(2, "Western"), _race(3, None, name="State")]


def test_pick_race_is_the_soonest_state_or_own_conference_race():
    assert raceday.pick_race(RACES, "Western")["id"] == 2
    assert raceday.pick_race(RACES, "Eastern  Blue")["id"] == 1
    assert raceday.pick_race(RACES, "Central")["id"] == 3  # only the state race
    assert raceday.pick_race(RACES, None)["id"] == 1  # unknown conference: soonest
    assert raceday.pick_race([], "Western") is None


def test_team_races_keeps_state_and_own_conference_only():
    assert [r["id"] for r in raceday.team_races(RACES, "Western")] == [2, 3]
    assert [r["id"] for r in raceday.team_races(RACES, None)] == [1, 2, 3]


def _row(event_id, rider_id, lap_secs, division="JV2", team="Pgh North", season=2026,
         conf="Western", gender="Male", course_id=5, laps=2):  # fmt: skip
    return {
        "event_id": event_id,
        "season": season,
        "event_order": event_id,
        "rider_id": rider_id,
        "division": division,
        "gender": gender,
        "loop_type": "HS",
        "laps": laps,
        "lap_secs": lap_secs,
        "conference": conf,
        "category_order": 1,
        "place": 1,
        "team": team,
        "course_id": course_id,
    }


def test_team_conference_comes_from_the_latest_row_this_season():
    rows = [
        _row(1, 1, 1000, conf="Central", season=2025),
        _row(2, 1, 1000, conf="Western"),
        _row(3, 1, 1000, conf="Eastern Blue", team="Other"),
    ]
    assert raceday.team_conference(rows, "PGH  north", 2026) == "Western"
    assert raceday.team_conference(rows, "Nobody", 2026) is None


def _grid_rows(team_of):
    """One JV2 event, riders 0-9; `team_of(i)` names the team."""
    return [
        {
            "canonical_id": i,
            "name": f"R{i}",
            "team": team_of(i),
            "division": "JV2",
            "conference": "Western",
            "conference_group": None,
            "event_id": 1,
            "event_name": "Race 1",
            "event_order": 1,
            "season": 2026,
            "z_pace": -1.0 + 0.2 * i,
            "z_lap": -1.0 + 0.2 * i,
            "bib": 100 + i,
        }
        for i in range(10)
    ]


def test_staging_lines_keep_the_team_only_and_include_unrated():
    rows = _grid_rows(lambda i: "Pgh North" if i % 2 == 0 else "Other")
    rows.append(
        {**rows[0], "canonical_id": 50, "name": "Newbie", "z_pace": None, "z_lap": None, "bib": 999}
    )
    grid = build_grid(rows, metric="lap", season=2026, group_size=4, row_size=2)
    lines = raceday.staging_lines([("HS", "Male", grid)], "pgh north")
    assert [line["canonical_id"] for line in lines] == [0, 2, 4, 6, 8, 50]
    assert lines[0]["category"] == "JV2 - Boys"
    assert lines[0]["rank"] == 1 and lines[0]["division_size"] == 11
    assert (lines[0]["group"], lines[0]["row"], lines[0]["color"]) == (1, 1, "Red")
    assert lines[2]["group"] == 2  # rider 4 is 5th overall: second box of four
    assert lines[-1]["basis"] is None and lines[-1]["group"] is None
    assert lines[-1]["plate"] == 999


def _field(event_id, course_id=5, season=2026, base=1000.0):
    """Ten JV2 boys; rider 0 fastest. Riders 0 and 1 ride for Pgh North."""
    return [
        _row(
            event_id,
            i,
            base + 20 * i,
            team="Pgh North" if i < 2 else "Other",
            course_id=course_id,
            season=season,
        )  # fmt: skip
        for i in range(10)
    ]


def test_course_baselines_use_the_latest_event_at_each_course():
    rows = _field(1, course_id=5, season=2025, base=1000) + _field(2, course_id=5, base=1300)
    rows += _field(3, course_id=7, base=1100)
    baselines = raceday.course_baselines(rows)
    assert set(baselines) == {(5, "HS", "Male"), (7, "HS", "Male")}
    # A 30% slower day at course 5 shows in the baseline chosen (event 2, not 1).
    assert baselines[(5, "HS", "Male")] > baselines[(7, "HS", "Male")]


def test_team_forecasts_place_and_time_for_the_teams_riders_only():
    rows = _field(1, season=2025) + _field(2)
    race = _race(9, "Western", course_id=5)
    laps = {"Male": {"JV2": 3}, "Female": {}}
    out = raceday.team_forecasts(rows, "Pgh North", 2026, race, laps, lambda p, f: "green")
    assert set(out) == {0, 1}
    fastest, second = out[0], out[1]
    assert fastest["place"] <= second["place"]
    assert fastest["place_low"] <= fastest["place"] <= fastest["place_high"]
    assert fastest["field"] == 10  # the last like-for-like field at this draw
    assert fastest["color"] == "green" and fastest["laps"] == 3
    # Rider 0 rides 1000 s laps on a day that is exactly typical for them:
    # three laps come to about 50 minutes, and the range brackets it.
    assert abs(fastest["est_minutes"] - 50) <= 2
    assert fastest["est_low"] < fastest["est_minutes"] < fastest["est_high"]
    assert math.isclose(fastest["lap_minutes"], 16.7, abs_tol=0.3)


def test_conference_race_cuts_the_field_to_that_conference():
    rows = _field(2)
    rows += [_row(2, 20 + i, 900 + 5 * i, conf="Central", team="Other") for i in range(10)]
    race_conf = _race(9, "Western")
    race_state = _race(9, None)
    laps = {"Male": {"JV2": 3}, "Female": {}}
    conf = raceday.team_forecasts(rows, "Pgh North", 2026, race_conf, laps, lambda p, f: "x")
    state = raceday.team_forecasts(rows, "Pgh North", 2026, race_state, laps, lambda p, f: "x")
    # Ten faster Central riders line up at the state race, so the same rider
    # is forecast further back there than at their own conference race.
    assert state[0]["place"] > conf[0]["place"]
    assert state[0]["field"] == 20 and conf[0]["field"] == 10


def test_no_baseline_at_a_new_course_leaves_the_time_blank():
    rows = _field(2, course_id=5)
    race = _race(9, "Western", course_id=99)
    out = raceday.team_forecasts(
        rows, "Pgh North", 2026, race, {"Male": {"JV2": 3}}, lambda p, f: "x"
    )
    assert "est_minutes" not in out[0] and out[0]["place"] >= 1


def test_merge_lines_attaches_forecasts_by_rider():
    lines = [{"canonical_id": 1, "name": "A"}, {"canonical_id": 2, "name": "B"}]
    merged = raceday.merge_lines(lines, {1: {"place": 3}})
    assert merged[0]["forecast"] == {"place": 3} and merged[1]["forecast"] is None


def test_a_rider_carried_on_two_grids_appears_once_where_they_raced_this_season():
    hs = build_grid(_grid_rows(lambda i: "Pgh North"), metric="lap", season=2026)
    # Rider 0 also sits on the MS grid on last season's MS Advanced average.
    ms_rows = [
        {**_grid_rows(lambda i: "Pgh North")[0], "division": "MS Advanced", "season": 2025,
         "event_id": 9, "event_order": 0}
    ]  # fmt: skip
    ms = build_grid(ms_rows, metric="lap", season=2026)
    assert ms["riders"][0]["basis"] == "prior"
    # MS grid listed first (sheet order puts MS girls before MS boys, HS first).
    lines = raceday.staging_lines([("MS", "Male", ms), ("HS", "Male", hs)], "Pgh North")
    mine = [line for line in lines if line["canonical_id"] == 0]
    assert len(mine) == 1 and mine[0]["age_group"] == "HS" and mine[0]["basis"] == "current"
    assert len(lines) == 10


def test_raceday_route_is_registered_before_the_greedy_team_page():
    from piclstats.web.app import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths.index("/team/{team_name:path}/raceday") < paths.index("/team/{team_name:path}")
