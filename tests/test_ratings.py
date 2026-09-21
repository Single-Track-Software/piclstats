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


# ── Rider form (the rider page's trend line) ────────────────────────────


def test_rider_form_is_stable_when_only_the_field_changes():
    from piclstats.web.ratings import rider_form

    # Rider 5 rides identical lap times at a state race (everyone there) and at
    # a conference race that only the slower half attends, on a course 20%
    # slower for all: the day effect sees through both, so the score holds.
    rows = [_row(1, i, 1000 + 20 * i, order=1) for i in range(12)]
    rows += [_row(2, i, (1000 + 20 * i) * 1.2, order=2, conf="Western") for i in range(4, 12)]
    form = rider_form(5, rows)
    assert [f["event_order"] for f in form] == [1, 2]
    assert abs(form[0]["score_pct"] - form[1]["score_pct"]) < 0.2
    assert form[0]["draw"] is None and form[1]["draw"] == "Western"
    assert form[1]["day_field"] == 8
    assert form[0]["league_field"] == 12  # the season's division, rider excluded, plus one
    assert form[0]["league_place"] == form[1]["league_place"]


def test_rider_form_reads_the_draw_from_the_event_name_when_results_lack_a_conference():
    from piclstats.web.ratings import rider_form

    rows = [
        {**_row(1, i, 1000 + 20 * i), "event_name": "Central Conf #2 - Coleman"} for i in range(10)
    ]
    assert rider_form(3, rows)[0]["draw"] == "Conference"
    assert rider_form(3, [_row(1, i, 1000 + 20 * i) for i in range(10)])[0]["draw"] is None


def test_rider_form_rating_follows_the_scores_and_absent_riders_get_nothing():
    from piclstats.web.ratings import rider_form

    rows = []
    for order in (1, 2, 3):
        rows += [_row(order, i, 1000 + 20 * i, order=order) for i in range(10)]
        rows.append(_row(order, 99, 1000 - 60 * order, order=order))  # improving every race
    form = rider_form(99, rows)
    assert [f["rating_pct"] for f in form] == sorted((f["rating_pct"] for f in form), reverse=True)
    assert form[-1]["rating_pct"] > form[-1]["score_pct"]  # rating lags the newest score
    assert rider_form(12345, rows) == []


# ── DNFs: the laps before the problem ─────────────────────────────────────


def _ctx(event_id, rider_id, laps, status="OK", place=1, order=None):
    return {
        "event_id": event_id,
        "season": 2026,
        "event_order": order if order is not None else event_id,
        "category": "JV2 - Male",
        "division": "JV2",
        "gender": "Male",
        "loop_type": "HS",
        "rider_id": rider_id,
        "status": status,
        "place": place,
        "dq_status": "ok",
        "laps": laps,
    }


def test_clean_laps_stop_at_the_broken_lap():
    from piclstats.web.ratings import clean_laps

    medians = [1000.0, 1000.0, 1000.0]
    assert clean_laps([990.0, 1010.0, 1700.0], medians) == [990.0, 1010.0]  # limped in on lap 3
    assert clean_laps([1600.0], medians) == []  # broke on lap 1: nothing to judge
    assert clean_laps([990.0, 1290.0], medians) == [990.0, 1290.0]  # slow but riding (< 1.3x)
    assert clean_laps([990.0, 1010.0, 1020.0, 1030.0], medians) == [
        990.0,
        1010.0,
        1020.0,
    ]  # no median → stop


def test_dnf_partial_scores_the_clean_laps_against_finishers_over_the_same_laps():
    from piclstats.web.ratings import dnf_partials, rider_form

    # 12 finishers, 2 laps each, lap 1 carries a start loop (10% longer for all).
    rows = [_row(1, i, 1000 + 20 * i, order=1) for i in range(12)]
    ctx = [_ctx(1, i, [(1000 + 20 * i) * 1.1, (1000 + 20 * i) * 0.9]) for i in range(12)]
    # Rider 99 rode lap 1 like rider 2 (a front runner) and then flatted.
    ctx.append(_ctx(1, 99, [(1000 + 20 * 2) * 1.1, 2500.0], status="DNF", place=None))
    scores = race_scores(rows)
    partials = dnf_partials(99, ctx, scores)
    assert len(partials) == 1
    p = partials[0]
    assert p["laps_used"] == 1 and p["laps_total"] == 2
    assert p["lap1_rank"] == 3 and p["lap1_field"] == 13  # riders 0 and 1 were quicker
    # Same lap-1 time as rider 2 → the same score as rider 2's full race.
    assert abs(p["x"] - scores[2][0].x) < 1e-3  # median in log vs linear space

    form = rider_form(99, rows, dnf_context=ctx)
    assert form == [
        {
            **form[0],
            "partial": True,
            "rating_pct": None,  # no race before it, so no rating to show
            "league_place": 3,
            "score_pct": round(scores[2][0].x * 100, 1),
        }
    ]
    # A partial never becomes a rating: rider 99 has no scores at all.
    assert 99 not in scores


def test_dnf_partial_needs_scored_finishers_and_a_clean_lap():
    from piclstats.web.ratings import dnf_partials

    rows = [_row(1, i, 1000 + 20 * i, order=1) for i in range(12)]
    scores = race_scores(rows)
    ctx = [_ctx(1, i, [1000 + 20 * i, 1000 + 20 * i]) for i in range(12)]
    # Broke on lap 1: no clean lap, no partial.
    assert dnf_partials(99, ctx + [_ctx(1, 99, [1900.0], status="DNF", place=None)], scores) == []
    # Two scored finishers is too thin to anchor on.
    thin = [_ctx(1, i, [1000 + 20 * i, 1000 + 20 * i]) for i in range(2)]
    assert dnf_partials(99, thin + [_ctx(1, 99, [1000.0], status="DNF", place=None)], scores) == []
