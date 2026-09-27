"""Public season schedule and its iCal feed (pure — no DB).

The schedule table (`scheduled_races`, entered by an admin) and the loaded
events (scraped results) are separate things: a scheduled race becomes a
results link once its event is loaded. They are matched by season, course
and name (the scraper's event names carry a leading year the schedule
does not), then by date when both have one, then by being the only pair at
that course in the season.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

_LEADING_YEAR = re.compile(r"^\s*(19|20)\d\d\s*[-–:]?\s*")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def name_key(name: str | None) -> str:
    """Lower-case letters and digits, leading year dropped: for matching names."""
    if not name:
        return ""
    return _NON_ALNUM.sub("", _LEADING_YEAR.sub("", name).lower())


def match_events(races: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    """Set `event_id` on each scheduled race whose loaded event can be identified.

    Only published events count (an unpublished one is not a results page).
    Each event is used at most once.
    """
    free = [e for e in events if e.get("is_published", True)]

    def take(pred) -> None:
        for race in races:
            if race.get("event_id"):
                continue
            hits = [e for e in free if e["course_id"] == race["course_id"] and pred(race, e)]
            if len(hits) == 1:
                race["event_id"] = hits[0]["id"]
                free.remove(hits[0])

    take(lambda r, e: name_key(r["name"]) == name_key(e.get("event_name")))
    take(lambda r, e: e.get("event_date") is not None and e["event_date"] == r["event_date"])
    # Last resort: the only scheduled race and the only loaded event at a course.
    for race in races:
        if race.get("event_id"):
            continue
        same_course = [r for r in races if r["course_id"] == race["course_id"]]
        hits = [e for e in free if e["course_id"] == race["course_id"]]
        if len(same_course) == 1 and len(hits) == 1:
            race["event_id"] = hits[0]["id"]
            free.remove(hits[0])


def events_as_schedule(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Loaded events in the schedule's shape, for a season with no schedule entered.

    Past seasons were loaded from results before the schedule table existed;
    the page still lists them, dated when the event has a date.
    """
    return [
        {
            "id": None,
            "name": e.get("event_name"),
            "course_id": e.get("course_id"),
            "course": e.get("course"),
            "location": e.get("location"),
            "event_date": e.get("event_date"),
            "conference": None,
            "canceled": False,
            "race_type": "rally" if e.get("event_type") == "rally" else "race",
            "event_id": e["id"],
        }
        for e in events
        if e.get("is_published", True)
    ]


def with_unscheduled(
    races: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The schedule plus any published event no schedule row claimed, in date order.

    Races loaded from results before the schedule was entered (the season
    opener, typically) would otherwise be missing from the page and the
    feed. Call after `match_events`. Undated events sort by race order after
    the dated ones.
    """
    matched = {r["event_id"] for r in races if r.get("event_id")}
    extra = events_as_schedule([e for e in events if e["id"] not in matched])
    order = {e["id"]: (e.get("event_order") or 0) for e in events}
    combined = list(races) + extra

    def key(r: dict[str, Any]):
        day = r.get("event_date")
        return (day is None, day or date.max, order.get(r.get("event_id"), 0), r["name"] or "")

    combined.sort(key=key)
    return combined


def for_conference(races: list[dict[str, Any]], conference: str | None) -> list[dict[str, Any]]:
    """State races plus one conference's (None = every race)."""
    if not conference:
        return list(races)
    wanted = " ".join(conference.split()).lower()
    return [
        r
        for r in races
        if not r.get("conference") or " ".join(r["conference"].split()).lower() == wanted
    ]


# ── iCal ─────────────────────────────────────────────────────────────────


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding: at most 75 octets per physical line."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    out: list[bytes] = []
    while data:
        cut = 75 if not out else 74
        # Never split a multi-byte character.
        while cut < len(data) and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(data[:cut])
        data = data[cut:]
    return "\r\n ".join(chunk.decode("utf-8") for chunk in out)


def build_ics(
    races: list[dict[str, Any]],
    base_url: str,
    season: int | None,
    now: datetime | None = None,
) -> str:
    """One all-day VEVENT per scheduled race; stable UIDs so re-syncs update in place.

    `races` need id, event_date, name, course, conference, and optionally
    location, canceled and event_id (the loaded results, linked in the
    description). A canceled race stays in the feed as STATUS:CANCELLED with
    a "CANCELED:" summary, so subscribers see it change rather than vanish.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = base_url.rstrip("/")
    name = f"PICL {season} races" if season else "PICL races"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PICL Stats//Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for r in races:
        day: date | None = r.get("event_date")
        if day is None:
            continue  # an undated loaded event is not a calendar entry
        course, location = r.get("course") or "", r.get("location") or ""
        # "Johnstown, PA" already names the Johnstown course.
        where = (
            location
            if course and course.lower() in location.lower()
            else ", ".join(p for p in (course, location) if p)
        )
        kind = r.get("conference") or "State race"
        if r.get("conference"):
            kind = f"{r['conference']} conference race"
        canceled = bool(r.get("canceled"))
        desc = [kind, f"Schedule: {base}/schedule"]
        if canceled:
            desc.insert(0, "Canceled — not rescheduled.")
        if r.get("event_id"):
            desc.append(f"Results: {base}/results?event_id={r['event_id']}")
        uid = f"race-{r['id']}" if r.get("id") else f"event-{r['event_id']}"
        summary = f"CANCELED: {r['name']}" if canceled else r["name"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@piclstats.com",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(summary)}",
        ]
        if canceled:
            lines.append("STATUS:CANCELLED")
        if where:
            lines.append(f"LOCATION:{_escape(where)}")
        lines.append(f"DESCRIPTION:{_escape(chr(10).join(desc))}")
        lines.append(f"URL:{base}/schedule")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
