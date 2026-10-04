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


def test_ordinals():
    from piclstats.web.standings import ordinal

    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
        "23rd",
        "101st",
        "111th",
    ]


def test_standing_in_finds_the_rider_and_marks_ties():
    from piclstats.web.standings import standing_in

    ranked = rank_by_total(
        [
            {"rider_id": 1, "name": "A", "total_points": 900},
            {"rider_id": 2, "name": "B", "total_points": 700},
            {"rider_id": 3, "name": "C", "total_points": 700},
            {"rider_id": 4, "name": "D", "total_points": 100},
        ]
    )
    assert standing_in(ranked, 1) == {"rank": 1, "label": "1st", "of": 4, "total_points": 900}
    assert standing_in(ranked, 3)["label"] == "T2nd"
    assert standing_in(ranked, 4)["label"] == "4th"
    assert standing_in(ranked, 99) is None


def test_conferences_run_west_to_east():
    from piclstats.web.queries import _conference_sort_key

    confs = {
        "Eastern Gold": "Eastern",
        "Central": "Central",
        "Eastern Blue": "Eastern",
        "Western": "Western",
        "Eastern": "Eastern",
        "Northern Tier": "",  # unknown region sorts last
    }
    ordered = sorted(confs, key=lambda n: _conference_sort_key(n, confs[n]))
    assert ordered == [
        "Western",
        "Central",
        "Eastern",
        "Eastern Blue",
        "Eastern Gold",
        "Northern Tier",
    ]
