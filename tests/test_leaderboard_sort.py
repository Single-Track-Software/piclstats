"""Leaderboard column sorting resolves to whitelisted ORDER BY clauses."""

import pytest

from piclstats.web.queries import RIDER_SORTS, TEAM_SORTS, leaderboard_order


def test_natural_direction_when_none_given():
    assert leaderboard_order(RIDER_SORTS, "avg_place", None) == (
        "avg_place",
        "asc",
        "avg_place ASC NULLS LAST",
    )
    assert leaderboard_order(RIDER_SORTS, "total_points", None)[1] == "desc"


def test_explicit_direction_wins():
    _, direction, order = leaderboard_order(TEAM_SORTS, "avg_points", "asc")
    assert direction == "asc"
    assert order == "avg_points ASC NULLS LAST"


@pytest.mark.parametrize("bad_metric", ["", "drop table", "rider_id"])
def test_unknown_metric_falls_back(bad_metric):
    metric, _, order = leaderboard_order(RIDER_SORTS, bad_metric, None)
    assert metric == "avg_points"
    assert order.startswith("avg_points DESC")


def test_unknown_direction_uses_natural():
    assert leaderboard_order(RIDER_SORTS, "best_place", "sideways")[1] == "asc"


def test_multi_column_sort_applies_direction_to_each():
    _, _, order = leaderboard_order(RIDER_SORTS, "division", "desc")
    assert order == "r.division DESC NULLS LAST, r.gender DESC NULLS LAST"


def test_ties_go_to_the_rider_with_more_races():
    # The ranking SQL must break a tie on the metric by races (more first),
    # then name: 575 avg over 3 races outranks 575 over 2.
    from piclstats.web import queries

    seen = {}

    class _Session:
        def execute(self, stmt, params=None):
            seen["sql"] = str(stmt)

            class _R:
                def all(self):
                    return []

            return _R()

    queries.leaderboard(_Session(), 2026, limit=10)
    order = seen["sql"].split("ORDER BY")[-1]
    assert order.index("avg_points DESC") < order.index("races DESC") < order.index("c.name")
