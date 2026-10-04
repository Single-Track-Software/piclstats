"""Prologue distance in pace math (course_prologues, 2026-10-03)."""

from piclstats.web import queries
from piclstats.web.riderstats import miles_ridden


def test_miles_ridden_adds_the_prologue_once():
    r = {"laps_ridden": 3, "loop_distance": 2.0, "prologue_miles": 0.4}
    assert miles_ridden(r) == 6.4
    assert miles_ridden({**r, "prologue_miles": None}) == 6.0
    assert miles_ridden({"laps_ridden": 3, "loop_distance": 2.0}) == 6.0


def test_a_short_first_lap_takes_distance_off():
    r = {"laps_ridden": 3, "loop_distance": 2.4, "prologue_miles": -0.5}
    assert abs(miles_ridden(r) - 6.7) < 1e-9


def test_no_laps_or_loop_means_no_miles():
    assert miles_ridden({"laps_ridden": 0, "loop_distance": 2.0, "prologue_miles": 0.4}) == 0.0
    assert miles_ridden({"laps_ridden": 3, "loop_distance": None, "prologue_miles": 0.4}) == 0.0


def test_every_pace_formula_uses_race_miles():
    # Pace must divide by laps x loop + prologue everywhere; a bare
    # laps x loop left in any query would silently ignore the prologue.
    import inspect

    src = inspect.getsource(queries)
    assert "* cl.distance_miles)" not in src
    assert "* cl.distance_miles, 0)" not in src
    assert "course_prologues p" in queries._LAP_JOINS
