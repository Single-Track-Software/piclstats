"""Rally segment lists (scraper/parser.py parse_rally_list): points and places, no laps."""

from __future__ import annotations

from piclstats.scraper.parser import parse_rally_list

_FIELDS = [
    "BIB", "ID", 'iif([RANK1]>0;iif([STATUS]<2;[RANK1];[TimeOrStatus]);"*")', "FLNAME",
    "Segment1Lap1", "Segment2Lap1", "Segment3Lap1", "Segment4Lap1", "SegmentTotal",
    "displayPoints", "if([T20]>0;[TIME20])",
]  # fmt: skip


def _row(bib, rank, name, points):
    return [str(bib), "9", rank, name, "4:01", "3:55", "5:10", "2:02", "15:08", str(points), ""]


def test_reads_category_place_points_and_never_laps():
    payload = {
        "DataFields": _FIELDS,
        "data": {
            "#1_": [],
            "#2_Varsity - Female": [
                _row(1, "1", "Lily Johnson", 575),
                _row(21, "2", "Perri Bruhns", 565),
            ],
        },
    }
    rows = parse_rally_list(payload)
    assert [(r.bib, r.name, r.place, r.points, r.status) for r in rows] == [
        (1, "LILY JOHNSON", 1, 575, "OK"),
        (21, "PERRI BRUHNS", 2, 565, "OK"),
    ]
    first = rows[0]
    assert (first.category, first.division, first.gender) == (
        "Varsity - Female",
        "Varsity",
        "Female",
    )
    assert first.team is None
    assert all(getattr(first, f"lap{i}") is None for i in range(1, 7))
    assert first.total_time is None


def test_unranked_and_status_rows():
    payload = {
        "DataFields": _FIELDS,
        "data": {
            "#5_JV3 - Male": [_row(3570, "DNF", "Elliott Igou", 100), _row(3571, "*", "X Y", "")]
        },
    }
    dnf, unranked = parse_rally_list(payload)
    assert (dnf.place, dnf.status, dnf.points) == (None, "DNF", 100)
    assert (unranked.place, unranked.status, unranked.points) == (None, "NR", None)
