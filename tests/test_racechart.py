"""Unit tests for the race-position bump chart (pure functions)."""

from __future__ import annotations

from piclstats.web.racechart import build_lap_chart, build_position_chart


def _row(bib, name, status, place, *laps):
    """A result row with lap splits in seconds; pad missing laps with None."""
    vals = list(laps) + [None] * (6 - len(laps))
    return {
        "bib": bib,
        "name": name,
        "team": "T",
        "status": status,
        "place": place,
        "lap1": vals[0],
        "lap2": vals[1],
        "lap3": vals[2],
        "lap4": vals[3],
        "lap5": vals[4],
        "lap6": vals[5],
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
    assert out == {
        "tracks": [],
        "n_laps": 0,
        "lap_labels": [],
        "field": 0,
        "finishers": 0,
        "shown": 0,
        "max_position": 0,
    }


def test_position_top_n_keeps_true_positions_and_scales_axis():
    # 5-rider, 2-lap field; keep the top 2 finishers but their positions stay
    # ranked against the whole field.
    rows = [
        _row(1, "P1", "OK", 1, 300, 300),
        _row(2, "P2", "OK", 2, 305, 305),
        _row(3, "P3", "OK", 3, 310, 310),
        _row(4, "P4", "OK", 4, 315, 315),
        _row(5, "P5", "OK", 5, 320, 320),
    ]
    out = build_position_chart(rows, top=2)
    assert out["field"] == 5  # full field still reported
    assert out["finishers"] == 5
    assert out["shown"] == 2
    assert [t.name for t in out["tracks"]] == ["P1", "P2"]
    assert out["max_position"] == 2  # axis scales to deepest shown position


def test_position_top_n_axis_reflects_a_riders_worst_position():
    # A finishes 1st but sat 3rd on lap 1 — the axis must reach 3 to show it.
    rows = [
        _row(1, "A", "OK", 1, 320, 280),  # slow start, fast finish -> wins
        _row(2, "B", "OK", 2, 300, 305),
        _row(3, "C", "OK", 3, 310, 320),
    ]
    out = build_position_chart(rows, top=1)
    a = out["tracks"][0]
    assert a.name == "A"
    assert a.positions == [3, 1]
    assert out["max_position"] == 3  # not 1 — A's lap-1 position is on screen


# ── Stacked lap-times (Gantt) ───────────────────────────────────────────


def test_lap_chart_durations_and_total():
    rows = [
        _row(1, "A", "OK", 1, 300, 310, 320, 330),
        _row(2, "B", "OK", 2, 305, 315, 325, 335),
    ]
    out = build_lap_chart(rows)
    assert out["n_laps"] == 4
    a = next(t for t in out["tracks"] if t.name == "A")
    assert a.laps == [300, 310, 320, 330]
    assert a.total == 1260
    assert a.complete is True


def test_lap_chart_short_bar_for_lapped_rider():
    rows = [
        _row(1, "A", "OK", 1, 300, 300, 300, 300),
        _row(2, "C", "OK", 3, 285, 315, 360),  # only 3 laps
    ]
    out = build_lap_chart(rows)
    c = next(t for t in out["tracks"] if t.name == "C")
    assert c.laps_done == 3
    assert c.complete is False
    assert len(c.laps) == 3  # no 4th segment — bar ends short


def test_lap_chart_ordered_fastest_first():
    rows = [
        _row(1, "Slow", "OK", 2, 400, 400),
        _row(2, "Fast", "OK", 1, 300, 300),
        _row(3, "Pulled", "OK", 3, 290),  # 1 lap — sorts last
    ]
    out = build_lap_chart(rows)
    assert [t.name for t in out["tracks"]] == ["Fast", "Slow", "Pulled"]


def test_lap_chart_colors_match_lap_count():
    rows = [_row(1, "A", "OK", 1, 300, 300, 300)]
    out = build_lap_chart(rows)
    assert len(out["lap_colors"]) == 3


def test_lap_chart_empty():
    assert build_lap_chart([]) == {
        "tracks": [],
        "series": [],
        "n_laps": 0,
        "lap_colors": [],
        "field": 0,
        "finishers": 0,
        "shown": 0,
    }


def test_lap_chart_top_n_truncates_tracks_and_series():
    rows = [
        _row(1, "P1", "OK", 1, 300, 310),
        _row(2, "P2", "OK", 2, 305, 315),
        _row(3, "P3", "OK", 3, 320, 330),
    ]
    out = build_lap_chart(rows, top=2)
    assert out["field"] == 3
    assert out["shown"] == 2
    assert [t.name for t in out["tracks"]] == ["P1", "P2"]
    # series must be rebuilt from just the shown riders.
    assert out["series"] == [[300, 305], [310, 315]]


def test_lap_chart_series_aligns_lap_to_rider():
    # Regression: each lap's series must be that lap's split for every rider,
    # not the rider-indexed diagonal that earlier broke the stacked bars.
    rows = [
        _row(1, "Fast", "OK", 1, 300, 310, 320),
        _row(2, "Mid", "OK", 2, 305, 315, 325),
        _row(3, "Pulled", "OK", 3, 290, 400),  # 2 laps only
    ]
    out = build_lap_chart(rows)
    names = [t.name for t in out["tracks"]]
    assert names == ["Fast", "Mid", "Pulled"]
    assert out["series"][0] == [300, 305, 290]  # lap 1 for all three
    assert out["series"][1] == [310, 315, 400]  # lap 2 for all three
    assert out["series"][2] == [320, 325, None]  # lap 3 — Pulled has none
    # Every lap series spans the full field.
    assert all(len(s) == out["field"] for s in out["series"])
