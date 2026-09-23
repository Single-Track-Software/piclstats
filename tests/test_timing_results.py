"""Rally reconciliation: pairing, flags, ranks, places, CSV (pure)."""

from datetime import datetime, timedelta, timezone

from piclstats.web.timing_results import (
    Adjustment,
    Crossing,
    DeviceStatus,
    RosterRider,
    Segment,
    csv_rows,
    effective_crossings,
    format_seconds,
    group_for_category,
    reconcile,
    split_category,
)

T0 = datetime(2027, 9, 19, 10, 0, tzinfo=timezone.utc)
SEG1 = Segment(1, 1, "Ridge", start_point_id=11, finish_point_id=12, distance_miles=0.5)
SEG2 = Segment(2, 2, "Creek", start_point_id=21, finish_point_id=22, rides_ms=False)
ROSTER = [
    RosterRider(100, "HS KID", "Team A", "JV1 - Male"),
    RosterRider(200, "MS KID", "Team B", "7th Grade - Female"),
]


def x(cid, point, secs, plate, **kw):
    return Crossing(cid, point, T0 + timedelta(seconds=secs), plate, **kw)


def test_category_split_and_group():
    assert split_category("JV1 - Male") == ("JV1", "Male")
    assert split_category("7th Grade - Girls") == ("7th Grade", "Female")
    assert split_category(None) == (None, None)
    assert group_for_category("Varsity - Female") == ("HS", True)
    assert group_for_category("MS Advanced - Male") == ("MS", True)
    assert group_for_category("Adult Open") == ("HS", False)


def test_format_seconds_matches_raceresult_totals():
    assert format_seconds(1938.8) == "32:18.8"
    assert format_seconds(3753.4) == "1:02:33.4"
    assert format_seconds(None) == ""


def test_effective_crossings_apply_the_latest_correction_in_a_chain():
    rows = [
        {
            "id": "a",
            "point_id": 11,
            "ts": T0,
            "plate": None,
            "kind": "tap",
            "supersedes": None,
            "voided": False,
            "note": None,
            "received_at": 1,
        },
        {
            "id": "b",
            "point_id": 11,
            "ts": T0,
            "plate": 100,
            "kind": "correction",
            "supersedes": "a",
            "voided": False,
            "note": "read plate",
            "received_at": 2,
        },
        {
            "id": "c",
            "point_id": 11,
            "ts": T0,
            "plate": 100,
            "kind": "correction",
            "supersedes": "b",
            "voided": True,
            "note": "double tap",
            "received_at": 3,
        },
    ]
    (eff,) = effective_crossings(rows)
    assert (eff.id, eff.plate, eff.voided, eff.note) == ("a", 100, True, "double tap")


def test_clean_rally_ranks_segments_and_places_within_category():
    crossings = [
        x("s1", 11, 0, 100),
        x("f1", 12, 90, 100),  # HS kid seg1 1:30
        x("s2", 21, 300, 100),
        x("f2", 22, 420, 100),  # HS kid seg2 2:00
        x("s3", 11, 30, 200),
        x("f3", 12, 110, 200),  # MS kid seg1 1:20 (MS does not ride seg2)
    ]
    rec = reconcile([SEG1, SEG2], crossings, ROSTER)
    assert rec.blocking == []
    hs = next(r for r in rec.riders if r.plate == 100)
    ms = next(r for r in rec.riders if r.plate == 200)
    assert hs.status == "OK" and hs.total_seconds == 210 and hs.place == 1
    assert ms.status == "OK" and ms.total_seconds == 80 and ms.place == 1
    assert list(ms.segments) == [1]  # only the segment MS rides
    assert hs.segments[1].rank == 1 and ms.segments[1].rank == 1  # ranked within category


def test_missing_finish_and_unknown_plate_block_and_dnf_is_info():
    crossings = [
        x("s1", 11, 0, 100),
        x("f1", 12, 90, 100),
        x("s2", 21, 300, 100),  # started seg2, never finished
        x("s9", 11, 40, 999),  # not on the roster
        x("n1", 12, 95, None),  # a finish with no plate
    ]
    rec = reconcile([SEG1, SEG2], crossings, ROSTER)
    kinds = sorted(f.kind for f in rec.flags)
    assert kinds == ["dnf", "no_plate", "start_no_finish", "unknown_plate"]
    hs = next(r for r in rec.riders if r.plate == 100)
    assert hs.status == "DNF" and hs.place is None and hs.total_seconds is None
    assert len(rec.unassigned) == 1
    # Accepting a flag by key clears it from the blocking list.
    key = next(f.key for f in rec.flags if f.kind == "start_no_finish")
    rec2 = reconcile([SEG1, SEG2], crossings, ROSTER, accepted_keys={key})
    assert all(f.kind != "start_no_finish" for f in rec2.blocking)


def test_duplicates_implausible_penalties_and_devices():
    crossings = [
        x("s1", 11, 0, 100),
        x("s1b", 11, 5, 100),
        x("f1", 12, 90, 100),  # two starts
        x("s2", 21, 300, 100),
        x("f2", 22, 301, 100),  # 1s for seg2: implausible
        x("s3", 11, 30, 200),
        x("f3", 12, 110, 200),
    ]
    rec = reconcile(
        [SEG1, SEG2],
        crossings,
        ROSTER,
        adjustments=[Adjustment(200, 10.0, "mechanical help in segment", segment_id=1)],
        devices=[DeviceStatus("dev-1", "Captain", 11, pending=2, drift_ms=1600, last_seen=None)],
    )
    kinds = sorted(f.kind for f in rec.flags)
    assert kinds == ["clock_drift", "dnf", "duplicate", "implausible", "unsynced"]
    ms = next(r for r in rec.riders if r.plate == 200)
    assert ms.penalty_seconds == 10.0 and ms.total_seconds == 90.0


def test_csv_has_one_column_per_segment():
    crossings = [x("s3", 11, 30, 200), x("f3", 12, 110, 200)]
    rec = reconcile([SEG1, SEG2], crossings, ROSTER)
    rows = csv_rows(rec, [SEG1, SEG2])
    assert rows[0] == [
        "plate",
        "name",
        "team",
        "category",
        "place",
        "status",
        "segment_1",
        "segment_2",
        "penalty_seconds",
        "total",
        "total_seconds",
    ]
    ms = next(r for r in rows[1:] if r[0] == "200")
    assert ms[4:] == ["1", "OK", "1:20.0", "", "", "1:20.0", "80.0"]
    hs = next(r for r in rows[1:] if r[0] == "100")
    assert hs[5] == "DNS"
