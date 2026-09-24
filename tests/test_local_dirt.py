"""Local dirt reconciliation: wave starts, stick order, category places, laps (pure)."""

from datetime import datetime, timedelta, timezone

from piclstats.web.timing_results import (
    Crossing,
    LocalRider,
    Wave,
    local_csv_rows,
    reconcile_local,
)

T0 = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)
START, FINISH = 11, 12
W1, W2 = Wave(1, 1, "Wave 1 — HS"), Wave(2, 2, "Wave 2 — MS")
ROSTER = [
    LocalRider(1, "AVA", "Parkland", "JV1 - Female", 1),
    LocalRider(2, "BEN", "Parkland", "JV1 - Male", 1),
    LocalRider(3, "CAL", "Emmaus", "JV1 - Male", 1),
    LocalRider(4, "DEE", "Emmaus", "7th Grade - Male", 2),
    LocalRider(5, "EVE", "Parkland", "7th Grade - Male", 2),
]


def x(cid, point, secs, plate=None, wave_id=None, **kw):
    return Crossing(cid, point, T0 + timedelta(seconds=secs), plate, wave_id=wave_id, **kw)


def test_two_lap_race_places_by_stick_order_and_times_from_wave_start():
    crossings = [
        x("ws1", START, 0, wave_id=1),
        x("ws2", START, 600, wave_id=2),
        # wave 1: BEN leads lap 1, CAL wins overall, AVA second, BEN drops to third
        x("f1", FINISH, 300, 2),
        x("f2", FINISH, 305, 3),
        x("f3", FINISH, 310, 1),
        x("f4", FINISH, 600, 3),
        x("f5", FINISH, 605, 1),
        x("f6", FINISH, 620, 2),
        # wave 2: DEE finishes both laps, EVE only one
        x("f7", FINISH, 900, 4),
        x("f8", FINISH, 905, 5),
        x("f9", FINISH, 1200, 4),
    ]
    rec = reconcile_local(
        [W1, W2], ROSTER, crossings, laps=2, start_point_id=START, finish_point_id=FINISH
    )
    assert rec.blocking == []
    by = {r.plate: r for r in rec.riders}
    assert [(by[p].place_wave, by[p].status) for p in (3, 1, 2)] == [
        (1, "OK"),
        (2, "OK"),
        (3, "OK"),
    ]
    assert by[3].elapsed_seconds == 600 and by[1].elapsed_seconds == 605 and by[2].laps == 2
    assert by[4].place_wave == 1 and by[4].elapsed_seconds == 600
    assert by[5].status == "DNF" and by[5].place_wave is None and by[5].laps == 1
    # category: JV1 - Male has BEN and CAL; CAL first
    assert by[3].place_category == 1 and by[2].place_category == 2 and by[1].place_category == 1
    assert sorted(f.kind for f in rec.flags) == ["dnf"]


def test_missing_wave_start_places_but_does_not_time():
    crossings = [x("f1", FINISH, 300, 2), x("f2", FINISH, 310, 1)]
    rec = reconcile_local(
        [W1], ROSTER[:3], crossings, laps=1, start_point_id=START, finish_point_id=FINISH
    )
    by = {r.plate: r for r in rec.riders}
    assert by[2].place_wave == 1 and by[2].elapsed_seconds is None and by[2].status == "OK"
    assert sorted(f.kind for f in rec.flags) == ["wave_no_start", "wave_no_start"]
    assert rec.blocking == []


def test_blocking_flags_for_double_start_no_rider_no_wave_and_extra_crossings():
    roster = ROSTER[:3] + [LocalRider(9, "ZED", None, None, None)]
    crossings = [
        x("ws1", START, 0, wave_id=1),
        x("ws1b", START, 5, wave_id=1),
        x("f1", FINISH, 300, 2),
        x("f2", FINISH, 310, None, note="tap 2"),
        x("f3", FINISH, 320, 9),
        x("f4", FINISH, 330, 2),
        x("f5", FINISH, 340, 77),
    ]
    rec = reconcile_local(
        [W1], roster, crossings, laps=1, start_point_id=START, finish_point_id=FINISH
    )
    assert sorted(f.kind for f in rec.blocking) == [
        "duplicate_wave_start",
        "no_plate",
        "no_wave",
        "too_many_crossings",
        "unknown_plate",
    ]
    assert rec.wave_starts[1].id == "ws1b"  # the latest start is the one in force
    assert len(rec.unassigned) == 1


def test_local_csv_skips_riders_who_never_started():
    crossings = [x("ws1", START, 0, wave_id=1), x("f1", FINISH, 300, 2)]
    rec = reconcile_local(
        [W1], ROSTER[:3], crossings, laps=1, start_point_id=START, finish_point_id=FINISH
    )
    rows = local_csv_rows(rec)
    assert rows[0][0] == "name" and len(rows) == 2
    assert (
        rows[1][:5] == ["BEN", "Parkland", "JV1 - Male", "Wave 1 — HS", "1"]
        and rows[1][7] == "5:00.0"
    )
