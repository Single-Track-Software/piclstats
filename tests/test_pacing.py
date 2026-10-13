"""Unit tests for the race read (pure functions in web/pacing.py)."""

from __future__ import annotations

import math

from piclstats.web.pacing import read_race, rider_pacing, summarize

ME = 1


def _row(rider_id, *laps, status="OK", place=1, event_id=10, category="JV - Male"):
    vals = list(laps) + [None] * (6 - len(laps))
    return {
        "event_id": event_id,
        "category": category,
        "rider_id": rider_id,
        "status": status,
        "place": place,
        "dq_status": "ok",
        **{f"lap{i + 1}": v for i, v in enumerate(vals)},
    }


def _field(lap1, lap2, n=9, start=100, **kw):
    """n riders who all ride lap1 then lap2 (a field that slows ~3% on lap 2)."""
    return [_row(start + i, lap1 + i, lap2 + i, place=i + 2, **kw) for i in range(n)]


def test_matching_the_field_is_even_pacing():
    rows = _field(600, 618) + [_row(ME, 600, 618)]
    read = read_race(ME, rows)
    assert read is not None and read["laps"] == 2
    out = summarize(read, usual=None)
    assert out["label"] == "even"
    assert out["lines"][0].startswith("Even pacing")


def test_slowing_more_than_the_field_is_went_out_hard():
    rows = _field(600, 618) + [_row(ME, 560, 640)]  # fast start, +14% on lap 2
    out = summarize(read_race(ME, rows), usual=None)
    assert out["label"] == "hard"
    assert "slowed" in out["lines"][0]


def test_holding_pace_better_than_the_field_is_finished_strong():
    rows = _field(600, 618) + [_row(ME, 640, 600)]
    out = summarize(read_race(ME, rows), usual=None)
    assert out["label"] == "strong"
    # A slow lap 1 gets the start-traffic caveat.
    assert any("back-row start" in line for line in out["lines"])


def test_lap_1_start_loop_cancels_against_the_field():
    # Lap 1 carries a long start loop for everyone: the raw times "speed up"
    # 40% but the rider did exactly what the field did.
    rows = _field(900, 540) + [_row(ME, 900, 540)]
    assert summarize(read_race(ME, rows), usual=None)["label"] == "even"


def test_place_after_each_lap_counts_everyone_on_that_lap():
    rows = _field(600, 618) + [_row(ME, 595, 640)]
    rows.append(_row(99, 700, status="DNF", place=None))  # rode lap 1 only
    read = read_race(ME, rows)
    first, second = read["lap_rows"]
    assert (first["place"], first["of"]) == (1, 11)
    assert second["of"] == 10
    assert second["place"] > first["place"]


def test_thin_field_or_single_lap_gives_no_read():
    assert read_race(ME, _field(600, 618, n=5) + [_row(ME, 600, 618)]) is None
    assert read_race(ME, _field(600, 618) + [_row(ME, 600)]) is None
    assert read_race(ME, _field(600, 618)) is None  # rider not in the category


def test_one_bad_lap_is_flagged_as_a_possible_issue():
    rows = [_row(200 + i, 600 + i, 618 + i, 610 + i) for i in range(9)]
    rows.append(_row(ME, 600, 800, 610))  # lap 2 about 30% slow, laps 1 and 3 normal
    out = summarize(read_race(ME, rows), usual=None)
    assert out["label"] == "issue"
    assert out["issue_lap"] == 2
    assert out["lines"][0].startswith("Lap 2 was")


def test_normal_fade_is_not_an_issue():
    rows = [_row(200 + i, 600 + i, 618 + i, 610 + i) for i in range(9)]
    rows.append(_row(ME, 600, 640, 650))  # steady fade, no single bad lap
    out = summarize(read_race(ME, rows), usual=None)
    assert out["label"] != "issue"


def _season(races):
    """(event_id, rider laps, field lap1, field lap2) → races + lap rows."""
    history, lap_rows = [], []
    for event_id, mine, f1, f2 in races:
        cat = "JV - Male"
        history.append({"event_id": event_id, "category": cat, "season": 2026})
        lap_rows += _field(f1, f2, event_id=event_id, category=cat)
        lap_rows.append(_row(ME, *mine, event_id=event_id, category=cat))
    return history, lap_rows


def test_usual_pace_comes_from_form_scores_not_the_category_field():
    # Same score every race, but the state race (event 3) has a much faster
    # field, so the rider sits further back against the category. They were
    # still on their usual pace.
    history, lap_rows = _season(
        [(1, (600, 618), 600, 618), (2, (600, 618), 600, 618), (3, (600, 618), 540, 556)]
    )
    out = rider_pacing(ME, history, lap_rows, scores={1: 0.0, 2: 0.0, 3: 0.0})
    assert out[(3, "JV - Male")]["lines"][-1] == "Right on your usual pace this season."


def test_a_big_lap_against_usual_pace_is_flagged_on_two_lap_races():
    history, lap_rows = _season(
        [(1, (600, 618), 600, 618), (2, (600, 618), 600, 618), (3, (600, 760), 600, 618)]
    )
    # Race 3's score carries the slow lap (average lap ≈ +11%).
    scores = {1: 0.0, 2: 0.0, 3: math.log((600 + 760) / (600 + 618))}
    out = rider_pacing(ME, history, lap_rows, scores=scores)
    race3 = out[(3, "JV - Male")]
    assert race3["label"] == "issue" and race3["issue_lap"] == 2
    assert "usual pace" in race3["lines"][0]
    assert race3["lines"][-1].startswith("Apart from lap 2, ")


def test_every_lap_slow_against_usual_is_an_off_day():
    history, lap_rows = _season(
        [(1, (600, 618), 600, 618), (2, (600, 618), 600, 618), (3, (690, 711), 600, 618)]
    )
    scores = {1: 0.0, 2: 0.0, 3: math.log(1.15)}
    out = rider_pacing(ME, history, lap_rows, scores=scores)
    assert out[(3, "JV - Male")]["label"] == "off"


def test_no_usual_pace_without_enough_scored_races():
    history, lap_rows = _season([(1, (600, 618), 600, 618), (2, (600, 618), 600, 618)])
    out = rider_pacing(ME, history, lap_rows, scores={1: 0.0, 2: 0.0})
    assert not any("usual" in line for line in out[(2, "JV - Male")]["lines"])
