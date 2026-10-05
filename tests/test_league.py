"""League page analysis shaping (conferences head to head, riders over time)."""

from piclstats.web.league import conference_comparison, division_comparison, riders_trend

WEST_EAST = ["Western", "Central", "Eastern Blue", "Eastern Gold"]


def _perf():
    return {
        "events": 3,
        "conferences": [
            {"conference": "Eastern Gold", "results": 240, "top10": 20, "riders": 200},
            {"conference": "Western", "results": 160, "top10": 30, "riders": 120},
            {"conference": "Central", "results": 600, "top10": 50, "riders": 300},
        ],
        "by_division": [
            {"conference": "Western", "division": "JV1", "results": 30, "avg_beaten": 55.0},
            {"conference": "Central", "division": "JV1", "results": 40, "avg_beaten": 50.0},
            {"conference": "Eastern Gold", "division": "JV1", "results": 4, "avg_beaten": 90.0},
            {"conference": "Central", "division": "Varsity", "results": 12, "avg_beaten": 48.0},
        ],
    }


def test_shares_order_and_finding():
    c = conference_comparison(_perf(), WEST_EAST)
    assert [r["conference"] for r in c["rows"]] == ["Western", "Central", "Eastern Gold"]
    west = c["rows"][0]
    assert west["field_share"] == 16.0 and west["top10_share"] == 30.0 and west["top10_gap"] == 14.0
    assert c["finding"].startswith(
        "Western riders took 30% of top-10 finishes from 16% of the field"
    )
    assert "Central took 50% from 60%" in c["finding"]  # furthest under its share


def test_division_table_skips_thin_cells_and_marks_best():
    d = division_comparison(_perf(), WEST_EAST)
    assert d["conferences"] == ["Western", "Central", "Eastern Gold"]
    assert [r["division"] for r in d["rows"]] == ["Varsity", "JV1"]
    jv1 = d["rows"][1]
    assert jv1["values"]["Eastern Gold"] is None  # only 4 results
    assert jv1["best"] == "Western"
    assert d["rows"][0]["best"] is None  # one conference scored: nothing to compare


def test_trend_labels_and_orders_series():
    rows = [
        {"season": 2023, "conference": None, "riders": 900},
        {"season": 2024, "conference": "Eastern", "riders": 500},
        {"season": 2024, "conference": "Western", "riders": 150},
        {"season": 2024, "conference": None, "riders": 2},
        {"season": 2025, "conference": "Eastern Blue", "riders": 280},
    ]
    t = riders_trend(rows, ["Western", "Central", "Eastern", "Eastern Blue", "Eastern Gold"])
    assert t["seasons"] == [2023, 2024, 2025]
    assert t["series"] == ["All riders", "Western", "Eastern", "Eastern Blue", "Unassigned"]
    assert t["counts"]["All riders"] == [900, 0, 0]
    assert t["counts"]["Unassigned"] == [0, 2, 0]
    assert t["totals"] == [900, 652, 280]
