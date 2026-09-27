"""Season recap (pure): rider lines, awards, attendance, totals."""

from __future__ import annotations

from piclstats.web.recap import awards, rider_lines, season_recap


def _row(cid, event, place, field=20, fade=None, laps=2, name=None, division="JV2",
         status="OK", dq="ok", points=None, order=None):  # fmt: skip
    return {
        "cid": cid,
        "name": name or f"R{cid}",
        "event_id": event,
        "event_name": f"Race {event}",
        "event_order": order if order is not None else event,
        "event_type": "points",
        "division": division,
        "gender": "Male",
        "place": place,
        "status": status,
        "points": points,
        "dq_status": dq,
        "field_size": field,
        "percentile": round((1 - place / field) * 100, 1) if place and dq != "excluded" else None,
        "laps_ridden": laps,
        "loop_distance": 4.0,
        "lap_fade": fade,
    }


def test_rider_lines_average_placed_clean_races_and_count_every_lap():
    rows = [
        _row(1, 1, 10, fade=2.0),
        _row(1, 2, 5, fade=-1.0),
        _row(1, 3, None, status="DNF", laps=1),
        _row(1, 4, 1, dq="excluded"),
    ]
    (x,) = rider_lines(rows, [{"id": 1, "delta_percentile": 12.0}])
    assert (x["started"], x["races"], x["dnfs"]) == (4, 2, 1)
    assert x["laps"] == 5 and x["miles"] == 20  # the excluded race's laps don't count
    assert x["avg_percentile"] == 62.5 and x["avg_fade"] == 0.5
    assert x["spread"] is None and x["climb"] is None  # two races: no trend yet
    assert x["delta_percentile"] == 12.0 and x["best_place"] == 5


def test_lines_are_division_first_then_best_average():
    rows = [_row(1, 1, 10, division="JV3"), _row(2, 1, 2, division="Varsity"),
            _row(3, 1, 5, division="JV3")]  # fmt: skip
    lines = rider_lines(rows, [])
    assert [x["id"] for x in lines] == [2, 3, 1]


def _team():
    rows = []
    # Rider 1: steady mid-pack, races every event, most laps.
    rows += [_row(1, e, 10, fade=1.0, laps=3) for e in (1, 2, 3, 4)]
    # Rider 2: climbs from 15th to 2nd, podiums twice, fades badly.
    rows += [_row(2, 1, 15, fade=8.0), _row(2, 2, 8, fade=6.0), _row(2, 3, 3, fade=7.0),
             _row(2, 4, 2, fade=9.0)]  # fmt: skip
    # Rider 3: two wins, misses two races, negative split.
    rows += [_row(3, 1, 1, fade=-2.0), _row(3, 3, 1, fade=-1.0)]
    # Rider 4: one race only — no averages.
    rows += [_row(4, 2, 4, fade=0.0)]
    return rows


def test_awards_pick_the_right_rider_with_minimum_samples():
    movers = [{"id": 1, "delta_percentile": 3.0}, {"id": 2, "delta_percentile": 20.0},
              {"id": 4, "delta_percentile": 50.0}]  # fmt: skip
    got = {a["label"]: a for a in awards(rider_lines(_team(), movers))}
    assert got["Most improved"]["id"] == 2  # rider 4's +50 is one race
    assert got["Strongest finish to the season"]["id"] == 2
    assert got["Most consistent"]["id"] == 1 and got["Most consistent"]["value"] == "±0 pts"
    assert got["Iron rider"]["id"] == 1 and got["Iron rider"]["value"] == "12 laps"
    assert got["Best pacer"]["id"] == 3 and got["Best pacer"]["value"] == "-1.5%"
    assert got["Top of the class"]["id"] == 3
    assert got["Podium leader"]["id"] == 3 and got["Podium leader"]["detail"] == "2 wins"


def test_awards_skip_when_nobody_qualifies():
    assert awards(rider_lines([_row(1, 1, 5, laps=0)], [])) == []  # one race: no average
    lines = rider_lines([_row(1, 1, 5, laps=2)], [])
    assert [a["label"] for a in awards(lines)] == ["Iron rider"]


def test_no_most_improved_when_everyone_got_worse():
    rows = [_row(1, 1, 10), _row(1, 2, 12)]
    got = [a["label"] for a in awards(rider_lines(rows, [{"id": 1, "delta_percentile": -5.0}]))]
    assert "Most improved" not in got


def test_recap_totals_and_perfect_attendance():
    recap = season_recap(_team(), [])
    assert recap["totals"] == {
        "riders": 4, "events": 4, "starts": 11, "laps": 26, "miles": 104,
        "wins": 2, "podiums": 4, "dnfs": 0,
    }  # fmt: skip
    assert {x["id"] for x in recap["perfect_attendance"]} == {1, 2}
    assert [e["event_id"] for e in recap["events"]] == [1, 2, 3, 4]
    assert "events" not in recap["lines"][0]
    assert season_recap([], []) is None


def test_single_race_season_has_no_perfect_attendance():
    recap = season_recap([_row(1, 1, 5), _row(2, 1, 6)], [])
    assert recap["perfect_attendance"] == []
