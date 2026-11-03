"""Season recap (pure): rider lines, the award slate, groups, attendance, totals."""

from __future__ import annotations

from piclstats.web.recap import awards, rider_lines, season_recap


def _row(cid, event, place, field=20, laps=None, n_laps=2, name=None, division="JV2",
         status="OK", dq="ok", points=None, order=None, lap1=None, behind=None,
         champ=False, first_season=2025, season=2026):  # fmt: skip
    """One rider × race row as `queries.team_season_rows` returns it.

    `laps` = the lap splits in seconds (only given when they add up to the ride
    time); `lap1` = (rank, field) after lap 1.
    """
    laps = list(laps) if laps else None
    return {
        "cid": cid,
        "name": name or f"R{cid}",
        "season": season,
        "event_id": event,
        "event_name": f"2026 Race #{event} - Loop at Venue {event}",
        "event_order": order if order is not None else event,
        "event_type": "points",
        "is_championship": champ,
        "division": division,
        "gender": "Male",
        "place": place,
        "status": status,
        "points": points,
        "dq_status": dq,
        "field_size": field,
        "percentile": round((1 - place / field) * 100, 1) if place and dq != "excluded" else None,
        "pct_behind": behind,
        "laps_ridden": len(laps) if laps else n_laps,
        "loop_distance": 4.0,
        "prologue_miles": 0,
        "laps": laps,
        "riding_secs": sum(laps) if laps else None,
        "lap1_rank": lap1[0] if lap1 else None,
        "lap1_field": lap1[1] if lap1 else None,
        "first_season": first_season,
    }


def _mover(cid, delta, prev_division=None):
    return {"id": cid, "delta_percentile": delta, "prev": {"division": prev_division}}


def test_rider_lines_average_placed_clean_races_and_count_every_lap():
    rows = [
        _row(1, 1, 10, laps=[600, 620, 610]),
        _row(1, 2, 5, laps=[600, 600, 590]),
        _row(1, 3, None, status="DNF", n_laps=1),
        _row(1, 4, 1, dq="excluded"),
    ]
    (x,) = rider_lines(rows, [_mover(1, 12.0, "JV2")])
    assert (x["started"], x["races"], x["dnfs"]) == (4, 2, 1)
    assert x["laps"] == 7 and x["miles"] == 28  # the excluded race's laps don't count
    assert x["avg_percentile"] == 62.5
    assert x["avg_fade"] == round(((610 - 620) / 620 * 100 + (590 - 600) / 600 * 100) / 2, 1)
    assert x["fade_races"] == 2 and x["avg_cv"] is not None
    assert x["best_mph"] == round(12 / 1790 * 3600, 2) and x["best_lap_mph"] == round(
        4 / 590 * 3600, 2
    )
    assert x["spread"] is None and x["climb"] is None  # two races: no trend yet
    assert x["delta_percentile"] == 12.0 and x["best_place"] == 5
    assert x["moved_up"] is False and x["rookie"] is False


def test_two_lap_races_give_pace_but_no_fade():
    (x,) = rider_lines([_row(1, 1, 3, laps=[600, 650])], [])
    assert x["fade_races"] == 0 and x["avg_fade"] is None and x["avg_cv"] is None
    assert x["best_mph"] is not None and x["best_lap_mph"] == round(4 / 650 * 3600, 2)


def test_lines_are_division_first_then_best_average():
    rows = [_row(1, 1, 10, division="JV3"), _row(2, 1, 2, division="Varsity"),
            _row(3, 1, 5, division="JV3")]  # fmt: skip
    lines = rider_lines(rows, [])
    assert [x["id"] for x in lines] == [2, 3, 1]


def test_starts_comebacks_championship_and_rookie_flags():
    rows = [
        _row(1, 1, 5, lap1=(12, 30)),  # +7 places
        _row(1, 2, 1, lap1=(1, 25)),  # holeshot
        _row(1, 3, 9, field=60, lap1=(4, 60), champ=True),
    ]
    (x,) = rider_lines(rows, [])
    assert x["holeshots"] == 1 and x["best_start"] == (1, 25, "Venue 2")
    assert x["comeback"] == {"gain": 7, "from": 12, "to": 5, "field": 20, "event": "Venue 1"}
    assert (x["champ_place"], x["champ_field"], x["champ_percentile"]) == (9, 60, 85.0)
    assert x["rookie"] is False
    (r,) = rider_lines([_row(2, 1, 5, first_season=2026)], [])
    assert r["rookie"] is True


def _team():
    rows = []
    # Rider 1: steady mid-pack, races every event, most laps, steadiest laps.
    rows += [_row(1, e, 10, laps=[600, 600, 600], lap1=(10, 20)) for e in (1, 2, 3, 4)]
    # Rider 2: climbs from 15th to 2nd, podiums twice, fades badly, moved up from JV3.
    rows += [_row(2, 1, 15, laps=[600, 600, 660], lap1=(15, 20)),
             _row(2, 2, 8, laps=[600, 600, 650], lap1=(8, 20)),
             _row(2, 3, 3, laps=[600, 600, 660], lap1=(6, 20)),
             _row(2, 4, 2, laps=[600, 600, 650], lap1=(2, 20), champ=True)]  # fmt: skip
    # Rider 3: two wins from the front, misses two races, negative split, closest to the winner.
    rows += [_row(3, 1, 1, laps=[600, 600, 580], lap1=(1, 20), behind=0.0),
             _row(3, 3, 1, laps=[600, 600, 590], lap1=(1, 20), behind=0.0)]  # fmt: skip
    # Rider 4: one race only — no averages. A rookie.
    rows += [_row(4, 2, 4, laps=[600, 600, 600], first_season=2026)]
    # Rider 5: middle schooler, rookie, two races.
    rows += [_row(5, 1, 6, division="8th Grade", first_season=2026, points=300),
             _row(5, 2, 4, division="8th Grade", first_season=2026, points=320)]  # fmt: skip
    return rows


def _movers():
    return [_mover(1, 3.0, "JV2"), _mover(2, 20.0, "JV3"), _mover(4, 50.0, "JV2")]


def test_awards_pick_the_right_rider_with_minimum_samples():
    got = {a["label"]: a for a in awards(rider_lines(_team(), _movers()), event_count=4)}
    win = {k: [w["id"] for w in a["winners"]] for k, a in got.items()}
    assert (
        win["Rider of the year"] == [3] and got["Rider of the year"]["winners"][0]["value"] == "95%"
    )
    assert (
        win["Podium master"] == [3]
        and got["Podium master"]["winners"][0]["value"] == "6 pts · 2 podiums"
    )
    assert win["Race winner"] == [3]
    assert win["Top-10 machine"] == [3, 1, 5]  # line order: division, then best average
    assert win["Top-half club"] == [3, 1, 5]  # best average first within a division
    assert win["Top points, middle school"] == [5] and "Top points, high school" not in win
    assert win["Best championship ride"] == [2]
    assert win["Closest to the front"] == [3]
    assert win["Most improved, year over year"] == [
        1
    ]  # rider 4's +50 is one race; rider 2 moved up
    assert win["Most improved, step up"] == [2]
    assert got["Most improved, step up"]["why"].startswith("Moved up from JV3 to JV2")
    assert win["Strongest finish to the season"] == [2]
    assert win["Fastest rider"] == [3]
    assert win["Holeshot"] == [3] and got["Holeshot"]["winners"][0]["value"] == "led lap 1 ×2"
    assert win["Closer"] == [3] and got["Closer"]["winners"][0]["value"] == "-2.5%"
    assert (
        win["Comeback ride"] == [2] and got["Comeback ride"]["winners"][0]["value"] == "+3 places"
    )
    assert win["Diesel"] == [1]
    assert win["Iron rider"] == [2, 1]  # 12 laps each: co-winners, best average first
    assert win["Perfect finisher"] == [2, 1]
    assert (
        win["Most consistent"] == [1] and got["Most consistent"]["winners"][0]["value"] == "±0 pts"
    )
    assert win["Rookie of the year"] == [5]  # rider 4 has one race
    assert [r["id"] for r in got["Rider of the year"]["runners_up"]] == [5, 2, 1]
    assert win["Steady climber"] == [2]  # 25 -> 60 -> 85 -> 90, never fell
    assert all(a["why"] and a["rule"] for a in got.values())


def test_steady_climber_needs_a_never_falling_line():
    rows = [_row(1, 1, 15), _row(1, 2, 8), _row(1, 3, 3)]
    (a,) = [a for a in awards(rider_lines(rows, [])) if a["label"] == "Steady climber"]
    assert a["winners"][0]["value"] == "25% → 60% → 85%"
    rows = [_row(1, 1, 15), _row(1, 2, 16), _row(1, 3, 3)]
    assert "Steady climber" not in {a["label"] for a in awards(rider_lines(rows, []))}


def test_awards_skip_when_nobody_qualifies():
    assert awards(rider_lines([_row(1, 1, 5, n_laps=0)], [])) == []  # one race: no average
    lines = rider_lines([_row(1, 1, 5, n_laps=2)], [])
    assert [a["label"] for a in awards(lines)] == ["Iron rider"]


def test_no_most_improved_when_everyone_got_worse():
    rows = [_row(1, 1, 10), _row(1, 2, 12)]
    got = [a["label"] for a in awards(rider_lines(rows, [_mover(1, -5.0, "JV2")]))]
    assert not any(label.startswith("Most improved") for label in got)


def test_recap_totals_sections_and_perfect_attendance():
    recap = season_recap(_team(), _movers())
    assert recap["totals"] == {
        "riders": 5, "events": 4, "starts": 13, "laps": 37, "miles": 148,
        "wins": 2, "podiums": 4, "dnfs": 0,
    }  # fmt: skip
    assert recap["sections"] == ["Results", "Improvement", "Engine", "Durability", "Rookie"]
    assert {x["id"] for x in recap["perfect_attendance"]} == {1, 2}
    assert [e["event_id"] for e in recap["events"]] == [1, 2, 3, 4]
    assert "events" not in recap["lines"][0]
    assert recap["group"] is None and recap["group_label"] is None
    assert season_recap([], []) is None


def test_group_narrows_lines_awards_and_totals():
    ms = season_recap(_team(), _movers(), "ms")
    assert ms["group"] == "ms" and ms["group_label"] == "Middle School"
    assert [x["id"] for x in ms["lines"]] == [5] and ms["totals"]["riders"] == 1
    assert {a["label"] for a in ms["awards"]} >= {"Rider of the year", "Rookie of the year"}
    hs = season_recap(_team(), _movers(), "hs")
    assert 5 not in {x["id"] for x in hs["lines"]} and hs["totals"]["riders"] == 4
    assert season_recap(_team(), _movers(), "nope")["group"] is None


def test_single_race_season_has_no_perfect_attendance():
    recap = season_recap([_row(1, 1, 5), _row(2, 1, 6)], [])
    assert recap["perfect_attendance"] == []
