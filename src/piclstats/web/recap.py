"""Season recap for a team: the banquet page (pure — no DB).

Coaches hand out awards at the end of the season and work them out from a
spreadsheet the night before. This turns one season of a team's results
into the usual ones — most improved, most consistent, most laps, best
pacer, top of the class, podium leader, perfect attendance — each with the
number behind it, plus a per-rider table so a coach can pick their own.

Every award has a minimum sample so a one-race wonder does not take it.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

from piclstats.web.staging import division_sort_key

# Placed scoring races needed before an average means anything.
MIN_RACES = 2
# ...and before a spread (consistency) or a trend (in-season climb) does.
MIN_RACES_TREND = 3


def _clean(r: dict[str, Any]) -> bool:
    return r.get("dq_status", "ok") != "excluded"


def _placed(r: dict[str, Any]) -> bool:
    return bool(r.get("place")) and _clean(r)


def _avg(values: list[float], digits: int = 1) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def rider_lines(rows: list[dict[str, Any]], movers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per rider: the season's counts and averages.

    `rows` = `queries.team_season_rows` (one per rider per scoring race);
    `movers` = `queries.team_rider_seasons`, for the change against last season.
    """
    delta = {m["id"]: m.get("delta_percentile") for m in movers}
    by_rider: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_rider.setdefault(r["cid"], []).append(r)

    out = []
    for cid, races in by_rider.items():
        races.sort(key=lambda r: (r.get("event_order") or 0, r["event_id"]))
        placed = [r for r in races if _placed(r)]
        pct = [float(r["percentile"]) for r in placed if r.get("percentile") is not None]
        fades = [float(r["lap_fade"]) for r in placed if r.get("lap_fade") is not None]
        latest = placed[-1] if placed else races[-1]
        laps = sum(int(r.get("laps_ridden") or 0) for r in races if _clean(r))
        miles = sum(
            int(r.get("laps_ridden") or 0) * float(r["loop_distance"])
            for r in races
            if _clean(r) and r.get("loop_distance")
        )
        out.append(
            {
                "id": cid,
                "name": latest["name"],
                "division": latest.get("division"),
                "gender": latest.get("gender"),
                "category": f"{latest.get('division') or '—'} {latest.get('gender') or ''}".strip(),
                "started": len(races),
                "races": len(placed),
                "dnfs": sum(1 for r in races if (r.get("status") or "") in ("DNF", "DSQ", "DQ")),
                "laps": laps,
                "miles": round(miles) if miles else None,
                "avg_percentile": _avg(pct),
                "spread": round(pstdev(pct), 1) if len(pct) >= MIN_RACES_TREND else None,
                "climb": round(pct[-1] - pct[0], 1) if len(pct) >= MIN_RACES_TREND else None,
                "avg_fade": _avg(fades),
                "fade_races": len(fades),
                "best_place": min(int(r["place"]) for r in placed) if placed else None,
                "wins": sum(1 for r in placed if int(r["place"]) == 1),
                "podiums": sum(1 for r in placed if int(r["place"]) <= 3),
                "points": sum(int(r["points"]) for r in placed if r.get("points") is not None),
                "delta_percentile": delta.get(cid),
                "events": {r["event_id"] for r in races},
            }
        )
    out.sort(
        key=lambda x: (
            division_sort_key(x["division"]),
            x["gender"] or "",
            -(x["avg_percentile"] if x["avg_percentile"] is not None else -1),
        )
    )
    return out


def _award(label, line, value, detail):
    return {
        "label": label,
        "id": line["id"],
        "name": line["name"],
        "category": line["category"],
        "value": value,
        "detail": detail,
    }


def awards(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The banquet awards, each with the number behind it. Skipped when nobody qualifies."""
    out: list[dict[str, Any]] = []

    improved = [x for x in lines if x["delta_percentile"] is not None and x["races"] >= MIN_RACES]
    if improved:
        top = max(improved, key=lambda x: x["delta_percentile"])
        if top["delta_percentile"] > 0:
            out.append(
                _award(
                    "Most improved",
                    top,
                    f"+{top['delta_percentile']:.0f} pts",
                    f"average percentile up from last season, now {top['avg_percentile']:.0f}%",
                )
            )

    climbers = [x for x in lines if x["climb"] is not None]
    if climbers:
        top = max(climbers, key=lambda x: x["climb"])
        if top["climb"] > 0:
            out.append(
                _award(
                    "Strongest finish to the season",
                    top,
                    f"+{top['climb']:.0f} pts",
                    "percentile, last race against the first",
                )
            )

    steady = [x for x in lines if x["spread"] is not None]
    if steady:
        top = min(steady, key=lambda x: (x["spread"], -(x["avg_percentile"] or 0)))
        out.append(
            _award(
                "Most consistent",
                top,
                f"±{top['spread']:.0f} pts",
                f"around a {top['avg_percentile']:.0f}% average over {top['races']} races",
            )
        )

    iron = [x for x in lines if x["laps"]]
    if iron:
        top = max(iron, key=lambda x: (x["laps"], x["started"]))
        miles = f" · ~{top['miles']} mi" if top["miles"] else ""
        out.append(
            _award(
                "Iron rider",
                top,
                f"{top['laps']} laps",
                f"over {top['started']} starts{miles}",
            )
        )

    pacers = [x for x in lines if x["avg_fade"] is not None and x["fade_races"] >= MIN_RACES]
    if pacers:
        top = min(pacers, key=lambda x: x["avg_fade"])
        sign = "+" if top["avg_fade"] > 0 else ""
        out.append(
            _award(
                "Best pacer",
                top,
                f"{sign}{top['avg_fade']:.1f}%",
                "last lap against the second, averaged (lower is steadier)",
            )
        )

    ranked = [x for x in lines if x["avg_percentile"] is not None and x["races"] >= MIN_RACES]
    if ranked:
        top = max(ranked, key=lambda x: (x["avg_percentile"], x["podiums"]))
        out.append(
            _award(
                "Top of the class",
                top,
                f"{top['avg_percentile']:.0f}%",
                f"of the category beaten on average · best place {top['best_place']}",
            )
        )

    podiums = [x for x in lines if x["podiums"]]
    if podiums:
        top = max(podiums, key=lambda x: (x["podiums"], x["wins"]))
        out.append(
            _award(
                "Podium leader",
                top,
                f"{top['podiums']} podium{'s' if top['podiums'] != 1 else ''}",
                f"{top['wins']} win{'s' if top['wins'] != 1 else ''}",
            )
        )

    return out


def season_recap(rows: list[dict[str, Any]], movers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Everything the recap page shows for one team season; None with no rows."""
    if not rows:
        return None
    lines = rider_lines(rows, movers)
    events = {
        r["event_id"]: {
            "event_id": r["event_id"],
            "event_name": r.get("event_name"),
            "event_order": r.get("event_order"),
        }
        for r in rows
    }
    event_count = len(events)
    every_race = [x for x in lines if len(x["events"]) == event_count] if event_count >= 2 else []
    totals = {
        "riders": len(lines),
        "events": event_count,
        "starts": sum(x["started"] for x in lines),
        "laps": sum(x["laps"] for x in lines),
        "miles": sum(x["miles"] or 0 for x in lines) or None,
        "wins": sum(x["wins"] for x in lines),
        "podiums": sum(x["podiums"] for x in lines),
        "dnfs": sum(x["dnfs"] for x in lines),
    }
    for x in lines:
        x.pop("events")
    return {
        "awards": awards(lines),
        "perfect_attendance": every_race,
        "lines": lines,
        "totals": totals,
        "events": sorted(events.values(), key=lambda e: (e.get("event_order") or 0, e["event_id"])),
    }
