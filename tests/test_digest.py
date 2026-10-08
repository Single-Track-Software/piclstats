"""Team race digest (web/digest.py): rows in, facts + highlights + Markdown out."""

from datetime import date

from piclstats.web.digest import build, fmt_gap


def _row(
    cid,
    name,
    event_id,
    order,
    place,
    field,
    pct,
    *,
    division="JV2",
    gender="Male",
    for_team=True,
    d=None,
    **kw,
):
    return {
        "cid": cid,
        "name": name,
        "team": "Lower Bucks Composite",
        "for_team": for_team,
        "event_id": event_id,
        "event_name": f"Race {order}",
        "event_order": order,
        "season": 2026,
        "race_date": d or date(2026, 9, 5 + 7 * order),
        "raceresult_id": 1000 + order,
        "course": "Belmont",
        "category": f"{division} - {gender}",
        "division": division,
        "gender": gender,
        "place": place,
        "status": kw.get("status", "OK"),
        "points": kw.get("points", 400),
        "total_time_raw": "45:00.0",
        "field_size": field,
        "conference": "Eastern Blue",
        "percentile": pct,
        "behind_secs": kw.get("behind", 61.5),
    }


def _standings(table):
    return lambda conference, division, gender: table


ROWS = [
    _row(1, "WOLF SNYDER", 10, 1, 6, 22, 72.7),
    _row(1, "WOLF SNYDER", 20, 2, 1, 22, 95.5),
    _row(2, "EMERSON POGGIOLI", 20, 2, 2, 2, 0.0, division="8th Grade", gender="Female"),
    _row(3, "MASON COVRLJAN", 10, 1, 5, 30, 83.3, division="7th Grade"),
    _row(3, "MASON COVRLJAN", 20, 2, 22, 29, 24.1, division="8th Grade"),
    _row(4, "ON ANOTHER TEAM", 20, 2, 3, 22, 86.4, for_team=False),
]
STAND = {1: {"rank": 1, "label": "1st", "of": 24, "total_points": 900}}


def test_only_races_since_the_date_and_only_team_riders():
    d = build(
        "Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings(STAND), today=date(2026, 10, 8)
    )
    assert [r["event"]["id"] for r in d["races"]] == [20]
    names = [r["name"] for r in d["races"][0]["riders"]]
    assert "ON ANOTHER TEAM" not in names and len(names) == 3


def test_rider_facts_previous_race_and_standing():
    d = build("Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings(STAND))
    wolf = next(r for r in d["races"][0]["riders"] if r["name"] == "WOLF SNYDER")
    assert wolf["place"] == 1 and wolf["field"] == 22 and wolf["behind_winner"] == "+1:01.5"
    assert wolf["previous"] == {"race": "Race 1", "place": 6, "field": 22, "field_beaten_pct": 72.7}
    assert wolf["standing"] == {"conference": "Eastern Blue", "rank": 1, "label": "1st", "of": 24}
    types = [h["type"] for h in wolf["highlights"]]
    assert types == ["podium", "season_best", "big_move", "standing"]


def test_podium_always_carries_the_field_size():
    d = build("Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings({}))
    emerson = next(r for r in d["races"][0]["riders"] if r["name"] == "EMERSON POGGIOLI")
    assert emerson["highlights"][0]["text"] == "2nd of 2 in 8th Grade Female"
    assert {"type": "first_race", "text": "First race of the season"} in emerson["highlights"]


def test_moving_up_is_named_not_scored_as_a_drop():
    d = build("Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings({}))
    mason = next(r for r in d["races"][0]["riders"] if r["name"] == "MASON COVRLJAN")
    assert [h["type"] for h in mason["highlights"]] == ["moved_up"]


def test_summary_and_markdown_come_from_the_same_facts():
    d = build("Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings(STAND))
    sm = d["races"][0]["summary"]
    assert sm == {"riders": 3, "finishers": 3, "podiums": 2, "top10": 2, "points": 1200}
    md = d["markdown"]
    assert md.startswith("## Race 2 — Belmont")
    assert "Lower Bucks Composite: 3 riders, 2 podiums, 2 top-10 finishes, 1,200 points." in md
    assert (
        "| [Wolf Snyder](https://piclstats.com/rider/1) | JV2 Male | 1 of 22 | 96% | 400 | 1st of 24 |"
        in md
    )


def test_empty_period():
    d = build("Lower Bucks Composite", ROWS, date(2026, 12, 1), _standings({}))
    assert d["races"] == [] and d["markdown"].startswith("No Lower Bucks Composite race results")


def test_gap_format():
    assert (
        fmt_gap(176) == "+2:56"
        and fmt_gap(4.3) == "+0:04.3"
        and fmt_gap(0) is None
        and fmt_gap(None) is None
    )


def test_markdown_gives_each_rider_one_highlight_line():
    d = build("Lower Bucks Composite", ROWS, date(2026, 9, 15), _standings(STAND))
    md = d["markdown"]
    assert (
        "- **Wolf Snyder:** 1st of 22 in JV2 Male — best finish of the season; "
        "beat 96% of the field, up from 73% last race; 1st of 24 in the Eastern Blue JV2 Male standings"
    ) in md
    # No podium or top 10 to lead with: the first clause leads, capitalised.
    assert "- **Mason Covrljan:** First race in 8th Grade after moving up from 7th Grade" in md
    assert md.count("Wolf Snyder:") == 1


def test_season_best_carries_the_field_size():
    rows = [
        _row(5, "SAM RIDER", 10, 1, 20, 30, 33.3),
        _row(5, "SAM RIDER", 20, 2, 14, 30, 53.3),
    ]
    d = build("Lower Bucks Composite", rows, date(2026, 9, 15), _standings({}))
    sam = d["races"][0]["riders"][0]
    assert sam["highlights"][0]["text"] == "Best finish of the season (14th of 30 in JV2 Male)"
    assert "- **Sam Rider:** Best finish of the season (14th of 30 in JV2 Male)" in d["markdown"]
