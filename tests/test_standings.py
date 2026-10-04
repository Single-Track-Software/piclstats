"""Conference standings: per-division tables, oldest first, ranked by total."""

from piclstats.web.standings import group_standings, rank_by_total


def _r(name, total, division="Varsity", gender="Male"):
    return {"name": name, "total_points": total, "division": division, "gender": gender}


def test_rank_by_total_shares_ties_and_skips():
    ranked = rank_by_total([_r("C", 300), _r("A", 400), _r("B", 300), _r("D", 100)])
    assert [(r["name"], r["rank"]) for r in ranked] == [("A", 1), ("B", 2), ("C", 2), ("D", 4)]


def test_missing_totals_rank_last():
    ranked = rank_by_total([_r("A", None), _r("B", 10)])
    assert [r["name"] for r in ranked] == ["B", "A"]


def test_divisions_oldest_first_male_before_female():
    rows = [
        _r("a", 1, "6th Grade", "Female"),
        _r("b", 1, "JV2", "Female"),
        _r("c", 1, "Varsity", "Female"),
        _r("d", 1, "JV2", "Male"),
        _r("e", 1, "8th Grade", "Male"),
        _r("f", 1, "Varsity", "Male"),
        _r("g", 1, "MS Advanced", "Male"),
    ]
    order = [(g["division"], g["gender"]) for g in group_standings(rows)]
    assert order == [
        ("Varsity", "Male"),
        ("Varsity", "Female"),
        ("JV2", "Male"),
        ("JV2", "Female"),
        ("MS Advanced", "Male"),
        ("8th Grade", "Male"),
        ("6th Grade", "Female"),
    ]


def test_each_group_is_ranked_on_its_own():
    groups = group_standings([_r("a", 50, "JV1"), _r("b", 90, "JV1"), _r("c", 10, "Varsity")])
    jv1 = next(g for g in groups if g["division"] == "JV1")
    assert [(r["name"], r["rank"]) for r in jv1["rows"]] == [("b", 1), ("a", 2)]
    assert groups[0]["division"] == "Varsity" and groups[0]["rows"][0]["rank"] == 1
