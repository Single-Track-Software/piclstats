"""Public schedule (pure): matching loaded events to scheduled races, iCal output."""

from __future__ import annotations

from datetime import date, datetime, timezone

from piclstats.web.schedule import (
    build_ics,
    events_as_schedule,
    for_conference,
    match_events,
    name_key,
)


def _race(id, name, course_id, day=date(2026, 10, 3), conference=None, location=None):
    return {
        "id": id,
        "name": name,
        "course_id": course_id,
        "event_date": day,
        "conference": conference,
        "course": f"Course {course_id}",
        "location": location,
    }


def _event(id, name, course_id, day=None, published=True):
    return {
        "id": id,
        "event_name": name,
        "course_id": course_id,
        "event_date": day,
        "is_published": published,
    }


def test_name_key_drops_the_scrapers_leading_year_and_punctuation():
    assert name_key("2026 Eastern Blue Conference #1 - Battle at Belmont") == name_key(
        "Eastern Blue Conference #1 – Battle at Belmont"
    )
    assert name_key("Flowin' at Fair Hill") == "flowinatfairhill"
    assert name_key(None) == ""


def test_match_by_name_then_date_then_only_pair_at_a_course():
    races = [
        _race(1, "Eastern Blue Conference #1 - Battle at Belmont", 10),
        _race(2, "Eastern Gold Conference #1 - Battle at Belmont", 10, day=date(2026, 9, 20)),
        _race(3, "Western Conference #1 - Ascent at Alameda", 11),
        _race(4, "State Championship", 5),
    ]
    events = [
        _event(184, "2026 Eastern Blue Conference #1 - Battle at Belmont", 10),
        _event(186, "2026 EG Conf #1 Belmont", 10, day=date(2026, 9, 20)),
        _event(185, "2026 Ascent at Alameda (Western)", 11),
        _event(190, "2026 State Champs", 5, published=False),
    ]
    match_events(races, events)
    assert [r.get("event_id") for r in races] == [184, 186, 185, None]


def test_an_event_matches_at_most_one_race():
    races = [_race(1, "Race A", 10), _race(2, "Race B", 10)]
    match_events(races, [_event(7, "Something else", 10)])
    assert [r.get("event_id") for r in races] == [None, None]  # two races, one event: ambiguous


def test_for_conference_keeps_state_and_that_conference():
    races = [_race(1, "S", 1), _race(2, "EB", 2, conference="Eastern Blue"),
             _race(3, "W", 3, conference="Western")]  # fmt: skip
    assert [r["id"] for r in for_conference(races, "eastern  blue")] == [1, 2]
    assert [r["id"] for r in for_conference(races, None)] == [1, 2, 3]


def test_ics_has_one_all_day_event_per_race_with_stable_uid_and_results_link():
    races = [
        {**_race(24, "State Championship - Flowin' at Fair Hill", 5, location="Elkton, MD"),
         "event_id": 200},
        _race(20, "Western Conference #3 - Boyce; Awards, too", 3, conference="Western"),
    ]  # fmt: skip
    ics = build_ics(
        races, "https://piclstats.com/", 2026, now=datetime(2026, 9, 27, tzinfo=timezone.utc)
    )
    lines = ics.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR" and lines[-1] == ""
    assert "X-WR-CALNAME:PICL 2026 races" in lines
    assert ics.count("BEGIN:VEVENT") == 2
    assert "UID:race-24@piclstats.com" in lines
    assert "DTSTART;VALUE=DATE:20261003" in lines and "DTEND;VALUE=DATE:20261004" in lines
    assert "DTSTAMP:20260927T000000Z" in lines
    assert "LOCATION:Course 5\\, Elkton\\, MD" in lines
    assert "SUMMARY:Western Conference #3 - Boyce\\; Awards\\, too" in lines
    assert any(line.startswith("DESCRIPTION:State race\\n") for line in lines)
    assert "Results: https://piclstats.com/results?event_id=200" in ics.replace("\r\n ", "")


def test_ics_folds_long_lines_at_75_octets_without_splitting_characters():
    races = [_race(1, "Ä" * 60 + " a very long race name that certainly needs folding", 1)]
    ics = build_ics(races, "https://x", 2026)
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    unfolded = ics.replace("\r\n ", "")
    assert "SUMMARY:" + "Ä" * 60 in unfolded


def test_events_stand_in_for_a_season_with_no_schedule():
    events = [
        _event(1, "2025 Race A", 10, day=date(2025, 9, 6)),
        _event(2, "2025 Race B", 11),
        _event(3, "2025 Hidden", 12, published=False),
    ]
    rows = events_as_schedule(events)
    assert [r["event_id"] for r in rows] == [1, 2] and rows[0]["id"] is None
    ics = build_ics(rows, "https://x", 2025)
    assert ics.count("BEGIN:VEVENT") == 1 and "UID:event-1@piclstats.com" in ics


def test_ics_location_is_not_the_course_twice():
    races = [_race(1, "R", 1, location="Course 1, PA")]
    assert "LOCATION:Course 1\\, PA" in build_ics(races, "https://x", 2026)


def test_canceled_race_stays_in_the_feed_as_cancelled():
    races = [{**_race(19, "Eastern Blue Conference #2", 105, conference="Eastern Blue"),
              "canceled": True}]  # fmt: skip
    ics = build_ics(races, "https://x", 2026)
    lines = ics.split("\r\n")
    assert "SUMMARY:CANCELED: Eastern Blue Conference #2" in lines
    assert "STATUS:CANCELLED" in lines
    assert any(line.startswith("DESCRIPTION:Canceled — not rescheduled.") for line in lines)
    assert "STATUS:CANCELLED" not in build_ics([_race(1, "R", 1)], "https://x", 2026)


def test_race_link_is_the_entrys_url_and_a_details_line():
    races = [{**_race(1, "R", 1), "url": "https://www.pamtb.org/race/1"}, _race(2, "S", 2)]
    ics = build_ics(races, "https://x", 2026)
    lines = ics.replace("\r\n ", "").split("\r\n")
    assert "URL:https://www.pamtb.org/race/1" in lines
    assert any(
        line.startswith("DESCRIPTION:State race\\nDetails: https://www.pamtb.org/race/1")
        for line in lines
    )
    assert "URL:https://x/schedule" in lines  # the race without a link points at the page
