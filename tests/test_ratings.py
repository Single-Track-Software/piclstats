"""Relative speed ratings and the future-race place forecast (pure — no DB)."""

from __future__ import annotations

import math

from piclstats.web.forecast import _place_color
from piclstats.web.ratings import (
    Rating,
    Score,
    build_future_matrix,
    build_roster,
    expected_field_size,
    fatigue_shift,
    field_history,
    place_distribution,
    race_scores,
    rate,
)


def _row(event_id, rider_id, lap_secs, division="JV2", laps=2, season=2026, order=None, conf=None):
    return {
        "event_id": event_id,
        "season": season,
        "event_order": order if order is not None else event_id,
        "rider_id": rider_id,
        "division": division,
        "gender": "Male",
        "loop_type": "HS",
        "laps": laps,
        "lap_secs": lap_secs,
        "conference": conf,
        "category_order": 1,
        "place": 1,
    }


def _score(x, season=2026, order=1):
    return Score(event_id=order, season=season, event_order=order, division="JV2", x=x)


def test_scores_cancel_the_course():
    # Same ten riders, second course 30% slower for everyone: identical scores.
    rows = [_row(1, i, 1000 + 20 * i) for i in range(10)]
    rows += [_row(2, i, (1000 + 20 * i) * 1.3) for i in range(10)]
    scores = race_scores(rows)
    for history in scores.values():
        assert math.isclose(history[0].x, history[1].x, abs_tol=1e-9)
    assert scores[0][0].x < 0 < scores[9][0].x  # faster than typical is negative


def test_event_effect_sees_through_a_weak_field():
    # Riders 0-9 race event 1 together. Event 2 draws only the slower half plus
    # nobody else, on an identical course: a field median would flatter them.
    rows = [_row(1, i, 1000 + 20 * i) for i in range(10)]
    rows += [_row(2, i, 1000 + 20 * i) for i in range(2, 10)]
    scores = race_scores(rows)
    assert math.isclose(scores[5][0].x, scores[5][1].x, abs_tol=1e-6)


def test_riders_pulled_early_and_thin_fields_do_not_score():
    rows = [_row(1, i, 1000 + i) for i in range(10)] + [_row(1, 99, 900, laps=1)]
    assert 99 not in race_scores(rows)
    assert race_scores([_row(1, i, 1000 + i) for i in range(5)]) == {}


def test_rating_weights_recent_races_and_discounts_last_season():
    assert rate([], 2026) is None
    improving = rate([_score(0.10, order=1), _score(0.00, order=2)], 2026)
    assert improving.mean < 0.05  # nearer the newer score
    carried = rate([_score(0.10, season=2025), _score(0.00, order=2)], 2026)
    assert carried.mean < improving.mean  # last season counts for less again


def test_rating_is_wider_with_less_or_older_evidence():
    one = rate([_score(0.0)], 2026)
    many = rate([_score(0.0, order=i) for i in range(1, 6)], 2026)
    stale = rate([_score(0.0, season=2025)], 2026)
    assert many.sd < one.sd < stale.sd
    assert stale.stale and not one.stale


def _field(n=39):
    return [Rating(mean=-0.2 + 0.4 * i / (n - 1), sd=0.07, races=3, stale=False) for i in range(n)]


def test_place_tracks_rating_and_range_brackets_it():
    fast = place_distribution(Rating(-0.25, 0.07, 3, False), _field())
    mid = place_distribution(Rating(0.0, 0.07, 3, False), _field())
    slow = place_distribution(Rating(0.25, 0.07, 3, False), _field())
    assert fast["place"] < mid["place"] < slow["place"]
    assert mid["field"] == 40 and 17 <= mid["place"] <= 24
    for cell in (fast, mid, slow):
        assert 1 <= cell["place_low"] <= cell["place"] <= cell["place_high"] <= cell["field"]


def test_uncertain_rider_gets_a_wider_range():
    sure = place_distribution(Rating(0.0, 0.05, 5, False), _field())
    unsure = place_distribution(Rating(0.0, 0.15, 1, True), _field())
    assert (unsure["place_high"] - unsure["place_low"]) > (sure["place_high"] - sure["place_low"])


def test_place_scales_to_the_expected_field():
    me = Rating(0.0, 0.07, 3, False)
    known = place_distribution(me, _field())
    bigger = place_distribution(me, _field(), expected_field=80)
    assert bigger["field"] == 80
    assert abs(bigger["place"] / 80 - known["place"] / 40) < 0.05


def test_fatigue_only_ever_costs():
    assert fatigue_shift(2, 4, 0.03) == math.log(1.06)
    assert fatigue_shift(4, 2, 0.03) == 0.0
    assert fatigue_shift(None, 3, 0.03) == 0.0
    me = Rating(0.0, 0.07, 3, False)
    assert (
        place_distribution(me, _field(), shift=0.06)["place"]
        > place_distribution(me, _field())["place"]
    )


def test_expected_field_prefers_this_season_like_for_like():
    rows = (
        [_row(1, i, 1000, season=2025, order=1) for i in range(30)]
        + [_row(2, i, 1000, season=2025, order=2, conf="Western") for i in range(12)]
        + [_row(3, i, 1000, season=2026, order=1) for i in range(40)]
    )
    history = field_history(rows)
    assert expected_field_size(history, 2026, "JV2", None) == 40  # this season's state race
    assert expected_field_size(history, 2026, "JV2", "Western") == 12  # last season's conference
    assert expected_field_size(history, 2026, "JV2", " western".title().strip()) == 12
    assert expected_field_size(history, 2026, "JV2", "Central") is None
    assert expected_field_size(history, 2026, "Varsity", None) is None


def test_roster_uses_latest_division_and_gives_unrated_riders_the_division_median():
    rows = [_row(1, i, 1000 + 80 * i) for i in range(10)]
    rows.append({**_row(1, 50, None), "division": "JV2"})  # splits didn't add up: no score
    rows += [_row(2, i, 1000 + 80 * i, division="JV1" if i == 0 else "JV2") for i in range(10)]
    roster = {e["rider_id"]: e for e in build_roster(rows, race_scores(rows), 2026)}
    assert roster[0]["division"] == "JV1"
    assert roster[50]["rating"].races == 0
    assert roster[50]["rating"].sd > roster[5]["rating"].sd
    assert build_roster(rows, race_scores(rows), 2027) == []  # nobody seen that season


def test_future_matrix_cuts_the_field_to_the_conference():
    rows = []
    for i in range(20):
        conf = "Western" if i < 10 else "Central"
        rows.append(_row(1, i, 1000 + 20 * i, conf=conf))
        rows.append(_row(1, 100 + i, 900 + 20 * i, division="JV1", laps=3, conf=conf))
    roster = build_roster(rows, race_scores(rows), 2026)
    races = [
        {"name": "State", "conference": None, "laps": {"JV2": 2, "JV1": 3}},
        {"name": "West", "conference": "Western", "laps": {"JV2": 2, "JV1": 3}},
    ]
    m = build_future_matrix(12, "JV2", roster, races, _place_color, 0.03, field_history(rows), 2026)
    assert set(m["divisions"]) == {"JV1", "JV2"}
    state, west = m["rows"]
    assert state["cells"]["JV2"]["own"] and not state["cells"]["JV1"]["own"]
    assert state["cells"]["JV1"]["extra_laps"] == 1
    assert state["cells"]["JV1"]["place"] > state["cells"]["JV2"]["place"] - 5  # faster field
    # Rider 12 is Central; every Western JV2 rider is quicker, so a Western race is harder.
    assert west["cells"]["JV2"]["field"] <= state["cells"]["JV2"]["field"]
    assert west["cells"]["JV2"]["color"] == "red"
    assert build_future_matrix(999, "JV2", roster, races, _place_color, 0.03, [], 2026) is None
