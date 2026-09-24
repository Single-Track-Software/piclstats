"""Rally timing setup: roster parsing and station codes (pure)."""

import pytest

from piclstats.web.timing import (
    CODE_LENGTH,
    RosterRow,
    new_station_code,
    parse_roster_lines,
    station_path,
)


def test_roster_lines_accept_comma_tab_or_pipe_and_skip_header_and_blanks():
    rows = parse_roster_lines(
        "plate,name,team,category\n"
        "1547, MAXWELL HOOVER, Cumberland Valley, JV1 - Male\n"
        "\n"
        "506\tMARK JOLLEY\tLower Merion Trailblazers Composite\tVarsity - Male\n"
        "2527 | MATTHEW MULLEN\n"
    )
    assert rows == [
        RosterRow(1547, "MAXWELL HOOVER", "Cumberland Valley", "JV1 - Male"),
        RosterRow(506, "MARK JOLLEY", "Lower Merion Trailblazers Composite", "Varsity - Male"),
        RosterRow(2527, "MATTHEW MULLEN", None, None),
    ]


def test_roster_only_skips_a_real_header_line():
    assert parse_roster_lines("Bib\tName\n7\tKID\n") == [RosterRow(7, "KID", None, None)]
    with pytest.raises(ValueError, match="Line 1"):
        parse_roster_lines("abc, nope\n")


def test_roster_reports_every_bad_line_and_duplicate_plates():
    with pytest.raises(ValueError) as exc:
        parse_roster_lines("12, A\nx, B\n12, C\n, D\n15, E\n")
    message = str(exc.value)
    assert "Line 2" in message and "Line 3" in message and "Line 4" in message
    assert "Line 5" not in message and "already given on line 1" in message


def test_station_codes_are_unambiguous_and_unique():
    codes = {new_station_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert len(code) == CODE_LENGTH
        assert not set(code) & set("0O1I")
    assert station_path("ABCD2345") == "/timing/s/ABCD2345"


def test_segment_form_parses_numbers_and_groups():
    from piclstats.web.timing import parse_segment_form

    seg = parse_segment_form(
        {
            "name": " Ridge ",
            "distance_miles": "1.4",
            "elevation_ft": "40",
            "elevation_loss_ft": "310",
            "rides_hs": "1",
        }
    )
    assert (seg.name, seg.distance_miles, seg.elevation_ft) == ("Ridge", 1.4, 40.0)
    assert seg.elevation_loss_ft == 310.0
    assert (seg.rides_hs, seg.rides_ms) == (True, False)
    blank = parse_segment_form({"name": "Creek", "rides_hs": "1", "rides_ms": "1"})
    assert blank.distance_miles is None and blank.elevation_ft is None


@pytest.mark.parametrize(
    "form",
    [
        {"name": "", "rides_hs": "1"},
        {"name": "X"},  # no group
        {"name": "X", "rides_ms": "1", "distance_miles": "two"},
        {"name": "X", "rides_ms": "1", "elevation_ft": "-5"},
    ],
)
def test_segment_form_rejects_bad_input(form):
    from piclstats.web.timing import parse_segment_form

    with pytest.raises(ValueError):
        parse_segment_form(form)


# ── Station sync helpers (pure) ────────────────────────────────────────────


def test_corrected_time_subtracts_the_device_offset():
    from datetime import datetime, timezone

    from piclstats.web.timing_station import corrected_ts

    # Device clock 2.5s ahead of the server: the crossing happened 2.5s earlier than it says.
    ts = corrected_ts(1_800_000_000_000, 2_500)
    assert ts == datetime.fromtimestamp(1_799_999_997.5, tz=timezone.utc)


def test_device_drift_keeps_the_largest_change():
    from piclstats.web.timing_station import device_offset_update

    assert device_offset_update(None, 0, 400) == (400, 0)
    assert device_offset_update(400, 0, 1900) == (1900, 1500)
    assert device_offset_update(1900, 1500, 1700) == (1700, 1500)


def test_normalize_crossing_accepts_a_tap_and_a_correction():
    from piclstats.web.timing_station import normalize_crossing

    tap = normalize_crossing(
        {
            "id": "3f2a9c1e-0b7d-4f3a-9c1e-0b7d4f3a9c1e",
            "device_ts_ms": 1_800_000_000_000,
            "offset_ms": 12,
            "plate": "1547",
        }
    )
    assert tap["kind"] == "tap" and tap["plate"] == 1547 and tap["voided"] is False
    fix = normalize_crossing(
        {
            "id": "c-abcdefgh",
            "kind": "correction",
            "supersedes": tap["id"],
            "device_ts_ms": 1_800_000_000_000,
            "voided": True,
            "note": "  ",
        }
    )
    assert fix["voided"] is True and fix["note"] is None and fix["supersedes"] == tap["id"]


@pytest.mark.parametrize(
    "raw",
    [
        {"id": "short", "device_ts_ms": 1_800_000_000_000},
        {"id": "3f2a9c1e0b7d4f3a", "device_ts_ms": 12},
        {"id": "3f2a9c1e0b7d4f3a", "device_ts_ms": 1_800_000_000_000, "kind": "photo"},
        {"id": "3f2a9c1e0b7d4f3a", "device_ts_ms": 1_800_000_000_000, "plate": "abc"},
        {"id": "3f2a9c1e0b7d4f3a", "device_ts_ms": 1_800_000_000_000, "kind": "correction"},
        {"id": "3f2a9c1e0b7d4f3a", "device_ts_ms": 1_800_000_000_000, "plate": True},
    ],
)
def test_normalize_crossing_rejects_bad_records(raw):
    from piclstats.web.timing_station import normalize_crossing

    with pytest.raises(ValueError):
        normalize_crossing(raw)


def test_slow_round_trip_is_not_a_clock_sample():
    """A sync that sat offline for minutes must not register as clock drift."""
    from piclstats.web.timing_station import MAX_SAMPLE_RTT_MS

    assert MAX_SAMPLE_RTT_MS == 1500
