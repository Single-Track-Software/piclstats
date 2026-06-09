"""Unit tests for the race-position bump chart (pure functions)."""

from __future__ import annotations

from piclstats.web.racechart import build_position_chart


def _row(bib, name, status, place, *laps):
    """A result row with lap splits in seconds; pad missing laps with None."""
    vals = list(laps) + [None] * (6 - len(laps))
    return {
        "bib": bib, "name": name, "team": "T", "status": status, "place": place,
        "lap1": vals[0], "lap2": vals[1], "lap3": vals[2],
        "lap4": vals[3], "lap5": vals[4], "lap6": vals[5],
    }


def test_position_is_rank_by_elapsed_time_each_lap():
    # A is slowest on lap 1 (300 vs 290 vs 285) then pulls clear.
    rows = [
        _row(1, "A", "OK", 1, 300, 300, 300, 300),
        _row(2, "B", "OK", 2, 290, 310, 320, 340),
        _row(3, "C", "OK", 3, 285, 315, 360, 400),
    ]
    out = build_position_chart(rows)
    assert out["n_laps"] == 4
    a = next(t for t in out["tracks"] if t.name == "A")
    assert a.positions == [3, 1, 1, 1]  # 3rd after lap 1, leads thereafter
    assert out["finishers"] == 3


def test_lapped_rider_line_ends_when_splits_stop():
    rows = [
        _row(1, "A", "OK", 1, 300, 300, 300, 300),
        _row(2, "B", "OK", 2, 305, 305, 305, 305),
        _row(3, "C", "OK", 3, 285, 400, 360),  # only 3 laps — lapped out
    ]
    out = build_position_chart(rows)
    c = next(t for t in out["tracks"] if t.name == "C")
    assert c.laps_done == 3
    assert c.complete is False
    # leads lap 1 (285), drops to 3rd as A/B pass, no position at lap 4
    assert c.positions == [1, 3, 3, None]
    assert out["finishers"] == 2


def test_dns_rider_excluded():
    rows = [
        _row(1, "A", "OK", 1, 300, 300),
        _row(2, "D", "DNS", None),  # no laps at all
    ]
    out = build_position_chart(rows)
    assert out["field"] == 1
    assert all(t.name != "D" for t in out["tracks"])


def test_dnf_after_one_lap_marked_incomplete():
    rows = [
        _row(1, "A", "OK", 1, 300, 300),
        _row(2, "E", "DNF", None, 290),  # crashed out after lap 1
    ]
    out = build_position_chart(rows)
    e = next(t for t in out["tracks"] if t.name == "E")
    assert e.complete is False
    assert e.positions == [1, None]  # led lap 1, then gone


def test_tracks_ordered_by_finish():
    rows = [
        _row(1, "Winner", "OK", 1, 300, 300),
        _row(2, "Second", "OK", 2, 310, 310),
        _row(3, "Lapped", "OK", 3, 290),  # 1 lap only — sorts last
    ]
    out = build_position_chart(rows)
    assert [t.name for t in out["tracks"]] == ["Winner", "Second", "Lapped"]


def test_empty_input():
    out = build_position_chart([])
    assert out == {"tracks": [], "n_laps": 0, "lap_labels": [], "field": 0, "finishers": 0}
