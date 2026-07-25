"""Parse raceresult.com JSON responses into dataclasses."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from piclstats.models import EventConfig, EventResults, RaceResult
from piclstats.scraper.client import fetch_config, fetch_results, resolve_list_name

logger = logging.getLogger(__name__)


def parse_time(raw: str) -> timedelta | None:
    """Parse a time string like '15:36', '15:36.01', '1:26:17.95', or '-'/'' into timedelta."""
    raw = raw.strip()
    if not raw or raw == "-":
        return None

    # Remove any non-time characters
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            # H:MM:SS or H:MM:SS.ff
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
        elif len(parts) == 2:
            # MM:SS or MM:SS.ff
            hours = 0
            minutes = int(parts[0])
            seconds = float(parts[1])
        elif len(parts) == 1:
            # Just seconds
            hours = 0
            minutes = 0
            seconds = float(parts[0])
        else:
            return None
    except (ValueError, IndexError):
        return None

    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def clean_name(raw: str) -> str:
    """Strip annotations like ' (PTS LEADER)' from rider names."""
    return re.sub(r"\s*\(PTS LEADER\)\s*$", "", raw).strip()


def parse_place_status(raw: str) -> tuple[int | None, str]:
    """Parse place field into (place, status). Returns (None, status_str) for DNF/DNS/etc."""
    raw = raw.strip()
    if not raw or raw == "*":
        return None, raw or "NR"
    try:
        return int(raw), "OK"
    except ValueError:
        return None, raw.upper()


def parse_category_key(key: str) -> tuple[int, str, str | None, str | None]:
    """Parse '#1_Varsity - Male' into (order, category, gender, division).

    Returns (category_order, full_category, gender, division).
    """
    # Strip leading '#' and split on first '_'
    key = key.lstrip("#")
    # Strip '///...' metadata suffixes (e.g., "///This Contest: 7///Total Laps: 2")
    if "///" in key:
        key = key.split("///")[0]
    if "_" in key:
        order_str, category = key.split("_", 1)
    else:
        order_str, category = "0", key

    try:
        order = int(order_str)
    except ValueError:
        order = 0

    # Parse gender from suffix
    gender = None
    if category.endswith(" - Male") or category.endswith(" - Boys"):
        gender = "Male"
    elif category.endswith(" - Female") or category.endswith(" - Girls"):
        gender = "Female"

    # Parse division (everything before " - Male/Female")
    division = None
    if " - " in category:
        division = category.rsplit(" - ", 1)[0].strip()
    else:
        division = category.strip()

    return order, category, gender, division


def _detect_field_layout(data_fields: list[str], sample_row: list[str]) -> dict[str, int]:
    """Detect which positional indices map to which fields.

    Uses DataFields formulas to identify each column's role. Known patterns:
      2022 (n=15): BIB ID PLC NAME CLUB SplitContest PTS LAP*4 PEN GAP TIME FLAG
      2023 (n=16): BIB ID PLC NAME CLUB SplitContest PTS LAP*5 PEN GAP TIME FLAG
      2024 State (n=16): BIB ID PLC NAME CLUB Conference PTS LAP*5 PEN TIME Group FLAG
      2024 Conf  (n=16): BIB ID PLC NAME CLUB PTS LAP*6 PEN GAP TIME FLAG
      2024 East  (n=16): BIB ID PLC NAME Conference CLUB PTS LAP*5 PEN TIME Group FLAG
      2025 (n=17): BIB ID PLC NAME CLUB School Conference PTS LAP*6 PEN TIME FLAG
    """
    n = len(data_fields)
    layout: dict[str, int] = {
        "bib": 0,
        "id": 1,
        "place": 2,
        "name": 3,
    }

    # Scan fields 4..n to find CLUB, Conference, School, Points by formula content
    team_idx = -1
    conference_idx = -1
    school_idx = -1
    points_idx = -1

    for i in range(4, n):
        df = data_fields[i]
        df_upper = df.upper()

        if df == "CLUB":
            team_idx = i
        elif df == "School":
            school_idx = i
        elif "CONFERENCE" in df_upper or "CONF" == df_upper:
            conference_idx = i
        elif "SPLITCONTEST" in df_upper:
            # 2022-2023: SplitContest is the group/category, not useful as conference
            conference_idx = -1  # explicitly skip
        elif "DISPLAYPOINTS" in df_upper or df == "DisplayPoints":
            points_idx = i
            break  # everything after points is laps/penalty/time

    # If we didn't find points by formula, look for it by position:
    # It's the first field after CLUB/Conference/School that isn't one of those
    if points_idx < 0:
        for i in range(4, n):
            if i in (team_idx, conference_idx, school_idx):
                continue
            df = data_fields[i]
            if "DisplayPoints" in df or "Points" in df:
                points_idx = i
                break

    layout["team"] = team_idx if team_idx >= 0 else 4
    layout["conference"] = conference_idx
    layout["school"] = school_idx
    layout["points"] = points_idx

    # Find TimeOrStatus — scan entire field list from end
    time_idx = -1
    for i in range(n - 1, 3, -1):
        if "TimeOrStatus" in data_fields[i]:
            time_idx = i
            break
    if time_idx < 0:
        for i in range(n - 1, 3, -1):
            df = data_fields[i]
            if (
                "TIME" in df.upper()
                and "LAP" not in df.upper()
                and "DISPLAY" not in df.upper()
                and "T20" not in df.upper()
            ):
                time_idx = i
                break
    layout["time"] = time_idx

    # Find penalty — look for TIME20 or PEN anywhere between CLUB and end
    penalty_idx = -1
    for i in range(4, n):
        df = data_fields[i]
        if "TIME20" in df.upper() or "PEN" in df.upper() or df.startswith("if([T20]"):
            penalty_idx = i
            break
    layout["penalty"] = penalty_idx

    # Laps — scan entire field list for LapTime/TIME10x patterns
    lap_num = 0
    for i in range(4, n):
        df = data_fields[i]
        if "LapTime" in df or "TIME10" in df:
            lap_num += 1
            if lap_num <= 6:
                layout[f"lap{lap_num}"] = i

    return layout


def parse_event(event_id: int, season: int, event_order: int) -> EventResults:
    """Fetch and parse a single event's results."""
    config_data = fetch_config(event_id)

    server = config_data.get("Server", config_data.get("server", "my-us-1.raceresult.com"))
    key = config_data.get("Key", config_data.get("key", ""))
    event_name = config_data.get("EventName", config_data.get("eventname", f"Event {event_id}"))
    list_name = resolve_list_name(config_data)

    event_config = EventConfig(
        raceresult_id=event_id,
        event_name=event_name,
        server=server,
        key=key,
        result_list_name=list_name,
    )

    results_data = fetch_results(server, event_id, key, list_name)
    data_fields: list[str] = results_data.get("DataFields", [])
    raw_groups: dict = results_data.get("data", {})

    # Flatten nested group structure. Some events have:
    #   data[category] = [rows]          (flat)
    #   data[category] = {sub_key: [rows]}  (nested)
    groups: dict[str, list[list[str]]] = {}
    for group_key, value in raw_groups.items():
        if isinstance(value, list):
            groups[group_key] = value
        elif isinstance(value, dict):
            # Nested — collect all row lists under sub-keys
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, list):
                    groups[group_key] = groups.get(group_key, []) + sub_value

    all_results: list[RaceResult] = []
    layout: dict[str, int] | None = None

    for group_key, rows in groups.items():
        if not rows:
            continue

        order, category, gender, division = parse_category_key(group_key)

        # Detect layout from first row if not yet done
        if layout is None and rows:
            layout = _detect_field_layout(data_fields, rows[0])
            logger.debug("Event %d field layout (n=%d): %s", event_id, len(data_fields), layout)

        for row in rows:
            try:
                result = _parse_row(row, layout, category, order, gender, division)
                all_results.append(result)
            except Exception:
                logger.warning(
                    "Failed to parse row in event %d, category %s: %s",
                    event_id,
                    category,
                    row,
                    exc_info=True,
                )

    logger.info(
        "Parsed event %d (%s): %d results across %d categories",
        event_id,
        event_name,
        len(all_results),
        len(groups),
    )

    return EventResults(
        config=event_config,
        season=season,
        event_order=event_order,
        results=all_results,
    )


def _parse_row(
    row: list[str],
    layout: dict[str, int],
    category: str,
    category_order: int,
    gender: str | None,
    division: str | None,
) -> RaceResult:
    """Parse a single result row using the detected field layout."""

    def get(field: str) -> str:
        idx = layout.get(field, -1)
        if idx < 0 or idx >= len(row):
            return ""
        return str(row[idx])

    bib_raw = get("bib")
    bib = int(bib_raw) if bib_raw.isdigit() else 0

    place, status = parse_place_status(get("place"))
    name = clean_name(get("name"))
    team = get("team") or None
    school = get("school") or None
    conference = get("conference") or None

    points_raw = get("points")
    points = int(points_raw) if points_raw.isdigit() else None

    # If time field contains DNF/DNS/DSQ and status was OK from place, override
    time_raw = get("time")
    if time_raw.upper() in ("DNF", "DNS", "DSQ", "DQ"):
        status = time_raw.upper()
        total_time = None
    else:
        total_time = parse_time(time_raw)

    return RaceResult(
        bib=bib,
        place=place,
        status=status,
        name=name,
        team=team,
        school=school,
        conference=conference,
        category=category,
        category_order=category_order,
        gender=gender,
        division=division,
        points=points,
        lap1=parse_time(get("lap1")),
        lap2=parse_time(get("lap2")),
        lap3=parse_time(get("lap3")),
        lap4=parse_time(get("lap4")),
        lap5=parse_time(get("lap5")),
        lap6=parse_time(get("lap6")),
        penalty=parse_time(get("penalty")),
        total_time=total_time,
        total_time_raw=time_raw,
        raw_row=row,
    )
