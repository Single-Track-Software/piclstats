"""Parsing of the season schedule forms (admin)."""

from datetime import date

import pytest

from piclstats.web.admin import parse_schedule_field, parse_schedule_lines

COURSES = {"granite": 1, "penn college": 2}
CONFERENCES = ["Central", "Eastern Blue", "Western"]


def test_field_state_or_blank_is_none():
    assert parse_schedule_field("State", CONFERENCES) is None
    assert parse_schedule_field(" state ", CONFERENCES) is None
    assert parse_schedule_field("", CONFERENCES) is None


def test_field_matches_conference_ignoring_case_and_spacing():
    assert parse_schedule_field("eastern   blue", CONFERENCES) == "Eastern Blue"
    with pytest.raises(ValueError, match="Northern"):
        parse_schedule_field("Northern", CONFERENCES)


def test_lines_round_trip_and_skip_blanks():
    races = parse_schedule_lines(
        "2026-09-27 | State Event #2 | Granite | State\n\n"
        "2026-10-04 | Central Conf #1 |  penn  college | central\n",
        COURSES,
        CONFERENCES,
    )
    assert races == [
        {
            "event_date": date(2026, 9, 27),
            "name": "State Event #2",
            "course_id": 1,
            "conference": None,
        },
        {
            "event_date": date(2026, 10, 4),
            "name": "Central Conf #1",
            "course_id": 2,
            "conference": "Central",
        },
    ]


def test_every_bad_line_is_reported_by_number():
    with pytest.raises(ValueError) as exc:
        parse_schedule_lines(
            "27/09/2026 | A | Granite | State\n"
            "2026-10-04 | B | Nowhere | State\n"
            "2026-10-11 | C | Granite\n"
            "2026-10-18 | D | Granite | State\n",
            COURSES,
            CONFERENCES,
        )
    message = str(exc.value)
    assert "Line 1" in message and "Line 2" in message and "Line 3" in message
    assert "Line 4" not in message
