"""Team race digest for the API: what happened to a team's riders, race by race.

Built for a Team Director's weekly news (a website post and a parent/rider
email). The site supplies facts and named highlights, and a ready-to-edit
Markdown summary built only from those facts, so every claim in a news
item traces back to a published result.

Input is queries.team_race_rows (one row per rider per points race this
season) plus a standings lookup; everything here is pure and tested.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from piclstats.web.staging import division_sort_key
from piclstats.web.standings import ordinal

SITE = "https://piclstats.com"
BIG_MOVE = 15.0  # points of "field beaten" gained since the rider's previous race
TOP_STANDING = 3  # conference standings places worth a highlight

# (conference, division, gender) -> {rider id: {"rank", "label", "of", "total_points"}}
StandingsLookup = Callable[[str, str, str], dict[int, dict[str, Any]]]


def fmt_gap(secs: float | None) -> str | None:
    """+2:56 / +0:04.3 style gap; None when not comparable."""
    if secs is None:
        return None
    secs = float(secs)
    if secs <= 0:
        return None
    m, s = divmod(secs, 60)
    return f"+{int(m)}:{s:04.1f}" if s % 1 else f"+{int(m)}:{int(s):02d}"


def _category(row: dict[str, Any]) -> str:
    return f"{row['division']} {row['gender'] or ''}".strip()


def build(
    team: str,
    rows: list[dict[str, Any]],
    since: date,
    standings: StandingsLookup,
    today: date | None = None,
) -> dict[str, Any]:
    """The digest for races on or after `since` that the team rode."""
    by_rider: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_rider.setdefault(r["cid"], []).append(r)

    race_ids = sorted(
        {
            r["event_id"]
            for r in rows
            if r["for_team"] and r["race_date"] and r["race_date"] >= since
        },
        key=lambda e: next(x["event_order"] for x in rows if x["event_id"] == e),
    )
    races = [_race(team, eid, rows, by_rider, standings) for eid in race_ids]
    return {
        "team": team,
        "since": since.isoformat(),
        "generated": (today or date.today()).isoformat(),
        "races": races,
        "markdown": _markdown(team, races),
    }


def _race(team, event_id, rows, by_rider, standings) -> dict[str, Any]:
    mine = [r for r in rows if r["event_id"] == event_id and r["for_team"]]
    first = mine[0]
    riders = []
    for r in sorted(
        mine, key=lambda x: (division_sort_key(x["division"]), x["gender"] or "", x["place"] or 999)
    ):
        history = by_rider[r["cid"]]
        earlier = [h for h in history if h["event_order"] < r["event_order"]]
        prev = earlier[-1] if earlier else None
        stand = (
            standings(r["conference"], r["division"], r["gender"]).get(r["cid"])
            if r["conference"]
            else None
        )
        rider = {
            "name": r["name"],
            "rider_url": f"{SITE}/rider/{r['cid']}",
            "category": _category(r),
            "division": r["division"],
            "gender": r["gender"],
            "status": r["status"],
            "place": r["place"],
            "field": r["field_size"],
            "field_beaten_pct": float(r["percentile"]) if r["percentile"] is not None else None,
            "points": r["points"],
            "time": r["total_time_raw"],
            "behind_winner": fmt_gap(r["behind_secs"]),
            "previous": (
                {
                    "race": prev["event_name"],
                    "place": prev["place"],
                    "field": prev["field_size"],
                    "field_beaten_pct": float(prev["percentile"])
                    if prev["percentile"] is not None
                    else None,
                }
                if prev
                else None
            ),
            "standing": (
                {
                    "conference": r["conference"],
                    "rank": stand["rank"],
                    "label": stand["label"],
                    "of": stand["of"],
                }
                if stand
                else None
            ),
        }
        rider["highlights"] = _highlights(r, rider, earlier)
        riders.append(rider)

    placed = [x for x in riders if x["place"]]
    summary = {
        "riders": len(riders),
        "finishers": sum(1 for x in riders if x["status"] == "OK"),
        "podiums": sum(1 for x in placed if x["place"] <= 3),
        "top10": sum(1 for x in placed if x["place"] <= 10),
        "points": sum(x["points"] or 0 for x in riders),
    }
    return {
        "event": {
            "id": first["event_id"],
            "name": first["event_name"],
            "date": first["race_date"].isoformat() if first["race_date"] else None,
            "course": first["course"],
            "results_url": f"{SITE}/results?event_id={first['event_id']}",
            "official_url": f"https://my.raceresult.com/{first['raceresult_id']}/"
            if first["raceresult_id"]
            else None,
        },
        "summary": summary,
        "riders": riders,
        "highlights": [{"rider": x["name"], **h} for x in riders for h in x["highlights"]],
    }


def _highlights(row, rider, earlier) -> list[dict[str, str]]:
    """Named, checkable highlights for one rider at one race (good news only)."""
    out: list[dict[str, str]] = []
    place, cat = row["place"], rider["category"]
    if place and place <= 3:
        # Always with the field size: 2nd of 2 is not 2nd of 30.
        out.append({"type": "podium", "text": f"{ordinal(place)} of {row['field_size']} in {cat}"})
    elif place and place <= 10:
        out.append({"type": "top10", "text": f"{ordinal(place)} of {row['field_size']} in {cat}"})
    same_div = [h for h in earlier if h["division"] == row["division"] and h["place"]]
    if place and same_div and place < min(h["place"] for h in same_div):
        out.append(
            {
                "type": "season_best",
                "text": f"Best finish of the season ({ordinal(place)} in {cat})",
            }
        )
    prev = earlier[-1] if earlier else None
    if (
        prev
        and prev["division"] != row["division"]
        and division_sort_key(row["division"]) < division_sort_key(prev["division"])
    ):
        out.append(
            {
                "type": "moved_up",
                "text": f"First race in {row['division']} after moving up from {prev['division']}",
            }
        )
    elif (
        prev
        and rider["field_beaten_pct"] is not None
        and prev["percentile"] is not None
        and rider["field_beaten_pct"] - float(prev["percentile"]) >= BIG_MOVE
    ):
        out.append(
            {
                "type": "big_move",
                "text": f"Beat {rider['field_beaten_pct']:.0f}% of the field, up from {float(prev['percentile']):.0f}% last race",
            }
        )
    if not earlier:
        out.append({"type": "first_race", "text": "First race of the season"})
    st = rider["standing"]
    if st and st["rank"] <= TOP_STANDING:
        out.append(
            {
                "type": "standing",
                "text": f"{st['label']} of {st['of']} in the {st['conference']} {cat} standings",
            }
        )
    return out


def _markdown(team: str, races: list[dict[str, Any]]) -> str:
    """A plain summary from the facts above, for a person or an agent to edit."""
    if not races:
        return f"No {team} race results in this period."
    parts = []
    for race in races:
        ev, sm = race["event"], race["summary"]
        head = f"## {ev['name']}" + (
            f" — {ev['course']}" if ev["course"] and ev["course"] not in ev["name"] else ""
        )
        parts.append(head)
        parts.append(
            f"{team}: {sm['riders']} rider{'s' if sm['riders'] != 1 else ''}, "
            f"{sm['podiums']} podium{'s' if sm['podiums'] != 1 else ''}, {sm['top10']} top-10 finish{'es' if sm['top10'] != 1 else ''}, "
            f"{sm['points']:,} points."
        )
        if race["highlights"]:
            parts.append(
                "**Highlights**\n\n"
                + "\n".join(f"- {h['rider'].title()}: {h['text']}" for h in race["highlights"])
            )
        lines = [
            "| Rider | Category | Place | Field beaten | Points | Standing |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in race["riders"]:
            place = f"{r['place']} of {r['field']}" if r["place"] else (r["status"] or "—")
            beaten = f"{r['field_beaten_pct']:.0f}%" if r["field_beaten_pct"] is not None else "—"
            st = f"{r['standing']['label']} of {r['standing']['of']}" if r["standing"] else "—"
            lines.append(
                f"| [{r['name'].title()}]({r['rider_url']}) | {r['category']} | {place} | {beaten} | {r['points'] or 0} | {st} |"
            )
        parts.append("\n".join(lines))
        parts.append(f"Full results: {ev['results_url']}")
    return "\n\n".join(parts) + "\n"
