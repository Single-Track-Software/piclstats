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
