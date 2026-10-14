"""TTC bonus parsing from raceresult season lists (pure parts of scraper/ttc.py)."""

from __future__ import annotations

from decimal import Decimal

from piclstats.scraper.ttc import parse_list, season_list_names

_FIELDS = [
    "BIB", "ID", "RANK2", "DisplayBib", "ucase([DisplayName])", "CLUB", "FinishCount",
    "#[RacesToDrop]/[EVENT.NumberOfRacesInSeason]", "LowScore", "DisplayUpgrades(1)",
    "SeriesScore", "TTCBonusPoints", "SeriesScore+TTCBonusPoints", "TTC_Hours",
]  # fmt: skip


def _row(bib, name, bonus, hours):
    return [str(bib), "1", "1", str(bib), name, "Team", "3", "3/3", "0", "450", "1350", str(bonus),
            "1360", str(hours)]  # fmt: skip


def test_parses_bonus_hours_and_category():
    payload = {
        "DataFields": _FIELDS,
        "data": {"#2_Varsity - Male": [_row(564, "FINN SWARTLEY", 21, 5)]},
    }
    (row,) = parse_list(payload)
    assert (row.category, row.bib, row.name, row.bonus) == (
        "Varsity - Male",
        564,
        "FINN SWARTLEY",
        21,
    )
    assert row.hours == Decimal("5")


def test_list_without_ttc_column_is_ignored():
    fields = [f for f in _FIELDS if "TTC" not in f]
    assert parse_list({"DataFields": fields, "data": {"#1_X - Male": [["1"] * len(fields)]}}) == []


def test_nested_groups_and_bad_rows():
    payload = {
        "DataFields": _FIELDS,
        "data": {"#1_Eastern": {"#1_JV2 - Female": [_row(2012, "A", 0, 0), ["bad"]]}},
    }
    rows = parse_list(payload)
    assert [(r.category, r.bib) for r in rows] == [("JV2 - Female", 2012)]


def test_season_list_names_prefer_ttc_lists():
    config = {
        "Lists": [
            {"Name": "02 - Result Lists|Individual Results - ALL"},
            {"Name": "03 - Season Overall Results|Individual Results - Overall"},
            {"Name": "03 - Season Overall Results|Individual Results - Overall-TTC Hours"},
            {"Name": "03 - Season Overall Results|Team Results - Overall"},
        ]
    }
    assert season_list_names(config) == [
        "03 - Season Overall Results|Individual Results - Overall-TTC Hours",
        "03 - Season Overall Results|Individual Results - Overall",
    ]
