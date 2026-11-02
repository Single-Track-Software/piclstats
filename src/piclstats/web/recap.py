"""Season recap for a team: the banquet page (pure — no DB).

Coaches hand out awards at the end of the season and work them out from a
spreadsheet the night before. This turns one season of a team's results
into a full slate — results, improvement, engine, durability, rookie —
each with the number behind it and a sentence a coach can read out, plus
a per-rider table so a coach can pick their own.

Every award has a minimum sample so a one-race wonder does not take it.
Awards can be run over the whole team or over the middle-school or
high-school half of it (`group`).
"""

from __future__ import annotations

from statistics import pstdev
from typing import Any

from piclstats.web.riderstats import miles_ridden
from piclstats.web.staging import division_sort_key

# Placed scoring races needed before an average means anything.
MIN_RACES = 2
# ...and before a spread (consistency) or a trend (in-season climb) does.
MIN_RACES_TREND = 3

MIDDLE_SCHOOL = {"6th Grade", "7th Grade", "8th Grade", "MS Advanced", "Middle School Advanced"}
GROUPS = {"ms": "Middle School", "hs": "High School"}


def _clean(r: dict[str, Any]) -> bool:
    return r.get("dq_status", "ok") != "excluded"


def _placed(r: dict[str, Any]) -> bool:
    return bool(r.get("place")) and _clean(r)


def _avg(values: list[float], digits: int = 1) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def is_middle_school(division: str | None) -> bool:
    return division in MIDDLE_SCHOOL


def _laps(r: dict[str, Any]) -> list[float]:
    """Lap splits in seconds, only when they add up to the ride time."""
    return [float(x) for x in (r.get("laps") or [])]


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _short_event(name: str | None) -> str:
    """'2026 Eastern Blue Conference #3 - Big Send at Blue Mountain & ...' -> 'Blue Mountain'."""
    if not name:
        return "a race"
    tail = name.split(" - ")[-1].split(" & ")[0]
    return tail.split(" at ")[-1].strip() if " at " in tail else tail.strip()


def rider_lines(rows: list[dict[str, Any]], movers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per rider: the season's counts and averages.

    `rows` = `queries.team_season_rows` (one per rider per scoring race);
    `movers` = `queries.team_rider_seasons`, for the change against last
    season and the division they were in then.
    """
    prev: dict[int, dict[str, Any]] = {m["id"]: m for m in movers}
    by_rider: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_rider.setdefault(r["cid"], []).append(r)

    out = []
    for cid, races in by_rider.items():
        races.sort(key=lambda r: (r.get("event_order") or 0, r["event_id"]))
        placed = [r for r in races if _placed(r)]
        pct = [float(r["percentile"]) for r in placed if r.get("percentile") is not None]
        behind = [float(r["pct_behind"]) for r in placed if r.get("pct_behind") is not None]
        latest = placed[-1] if placed else races[-1]
        laps_total = sum(int(r.get("laps_ridden") or 0) for r in races if _clean(r))
        miles = sum(miles_ridden(r) for r in races if _clean(r))

        # Pace and pacing from the lap splits (only races whose splits add up).
        fades: list[float] = []
        cvs: list[float] = []
        mph: list[float] = []
        lap_mph: list[float] = []
        for r in races:
            laps = _laps(r)
            loop = float(r.get("loop_distance") or 0)
            secs = float(r.get("riding_secs") or 0)
            if laps and secs > 0:
                race_miles = miles_ridden(r)
                if race_miles:
                    mph.append(race_miles / secs * 3600)
                if len(laps) >= 2 and loop:
                    lap_mph.append(loop / min(laps[1:]) * 3600)
                if len(laps) >= 3:
                    fades.append((laps[-1] - laps[1]) / laps[1] * 100)
                    pure = laps[1:]
                    cvs.append(pstdev(pure) / (sum(pure) / len(pure)) * 100)

        # Starts and comebacks: position after lap 1 against the finish.
        holeshots = 0
        best_start: tuple[int, int, str] | None = None
        comeback: dict[str, Any] | None = None
        for r in placed:
            rank, field = r.get("lap1_rank"), r.get("lap1_field")
            if not rank or not field:
                continue
            rank, field = int(rank), int(field)
            if rank == 1:
                holeshots += 1
            if best_start is None or (rank, -field) < (best_start[0], -best_start[1]):
                best_start = (rank, field, _short_event(r.get("event_name")))
            gain = rank - int(r["place"])
            if comeback is None or gain > comeback["gain"]:
                comeback = {
                    "gain": gain,
                    "from": rank,
                    "to": int(r["place"]),
                    "field": int(r.get("field_size") or field),
                    "event": _short_event(r.get("event_name")),
                }

        champ = next((r for r in placed if r.get("is_championship")), None)
        mover = prev.get(cid) or {}
        prev_division = (mover.get("prev") or {}).get("division")
        season = latest.get("season")
        out.append(
            {
                "id": cid,
                "name": latest["name"],
                "division": latest.get("division"),
                "gender": latest.get("gender"),
                "category": f"{latest.get('division') or '—'} {latest.get('gender') or ''}".strip(),
                "ms": is_middle_school(latest.get("division")),
                "started": len(races),
                "races": len(placed),
                "dnfs": sum(1 for r in races if (r.get("status") or "") in ("DNF", "DSQ", "DQ")),
                "laps": laps_total,
                "miles": round(miles) if miles else None,
                "avg_percentile": _avg(pct),
                "percentiles": pct,
                "spread": round(pstdev(pct), 1) if len(pct) >= MIN_RACES_TREND else None,
                "climb": round(pct[-1] - pct[0], 1) if len(pct) >= MIN_RACES_TREND else None,
                "avg_behind": _avg(behind),
                "behind_races": len(behind),
                "avg_fade": _avg(fades),
                "fade_races": len(fades),
                "avg_cv": _avg(cvs, 2),
                "best_mph": round(max(mph), 2) if mph else None,
                "best_lap_mph": round(max(lap_mph), 2) if lap_mph else None,
                "holeshots": holeshots,
                "best_start": best_start,
                "comeback": comeback,
                "best_place": min(int(r["place"]) for r in placed) if placed else None,
                "wins": sum(1 for r in placed if int(r["place"]) == 1),
                "podiums": sum(1 for r in placed if int(r["place"]) <= 3),
                "podium_points": sum({1: 3, 2: 2, 3: 1}.get(int(r["place"]), 0) for r in placed),
                "top10": sum(1 for r in placed if int(r["place"]) <= 10),
                "top_half": sum(1 for r in placed if float(r.get("percentile") or 0) >= 50),
                "points": sum(int(r["points"]) for r in placed if r.get("points") is not None),
                "delta_percentile": mover.get("delta_percentile"),
                "prev_division": prev_division,
                "moved_up": bool(prev_division and prev_division != latest.get("division")),
                "rookie": bool(season is not None and latest.get("first_season") == season),
                "champ_place": int(champ["place"]) if champ else None,
                "champ_field": int(champ.get("field_size") or 0) if champ else None,
                "champ_percentile": float(champ["percentile"])
                if champ and champ.get("percentile") is not None
                else None,
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


# ── Awards ──────────────────────────────────────────────────────────


def _who(x: dict[str, Any], value: str) -> dict[str, Any]:
    return {"id": x["id"], "name": x["name"], "category": x["category"], "value": value}


def _award(section, label, winners, why, runners_up=(), rule=""):
    return {
        "section": section,
        "label": label,
        "winners": winners,
        "why": why,
        "runners_up": list(runners_up),
        "rule": rule,
    }


def _pct(x: float) -> str:
    return f"{x:.0f}%"


def _signed(x: float, digits: int = 0) -> str:
    return f"{x:+.{digits}f}"


def _place_of(x: dict[str, Any]) -> str:
    return f"{_ordinal(x['champ_place'])} of {x['champ_field']}"


def _single(section, label, pool, key, value, why, rule="", reverse=True, top_n=3, ties=False):
    """One winner by `key` with the next riders as runners-up; None when the pool is empty.

    With `ties`, every rider level on the key shares the award (co-winners)
    instead of one being picked by list order.
    """
    if not pool:
        return None
    ranked = sorted(pool, key=key, reverse=reverse)
    top = ranked[0]
    winners = [x for x in ranked if key(x) == key(top)] if ties else [top]
    rest = ranked[len(winners) : len(winners) + top_n]
    return _award(
        section,
        label,
        [_who(x, value(x)) for x in winners],
        why(top),
        [_who(x, value(x)) for x in rest],
        rule,
    )


def _listing(section, label, pool, value, why, rule=""):
    """Every rider in the pool wins (a club, not a contest)."""
    if not pool:
        return None
    return _award(section, label, [_who(x, value(x)) for x in pool], why, rule=rule)


def awards(lines: list[dict[str, Any]], event_count: int = 0) -> list[dict[str, Any]]:
    """The banquet slate, each with the number behind it. Skipped when nobody qualifies."""
    out: list[dict[str, Any] | None] = []
    ranked = [x for x in lines if x["avg_percentile"] is not None and x["races"] >= MIN_RACES]

    # ── Results ──
    out.append(
        _single(
            "Results",
            "Rider of the year",
            ranked,
            key=lambda x: (x["avg_percentile"], x["wins"], x["podiums"]),
            value=lambda x: _pct(x["avg_percentile"]),
            why=lambda x: (
                f"Beat {_pct(x['avg_percentile'])} of the category on average over "
                f"{x['races']} races; {x['wins']} win{'s' if x['wins'] != 1 else ''}, "
                f"{x['podiums']} podium{'s' if x['podiums'] != 1 else ''}, best place "
                f"{_ordinal(x['best_place'])}"
                + (f"; {_place_of(x)} at the championship." if x["champ_place"] else ".")
            ),
            rule="share of the category beaten, averaged; at least 2 races",
        )
    )
    out.append(
        _single(
            "Results",
            "Podium master",
            [x for x in lines if x["podiums"]],
            key=lambda x: (x["podium_points"], x["wins"], x["podiums"]),
            value=lambda x: (
                f"{x['podium_points']} pts · {x['podiums']} podium{'s' if x['podiums'] != 1 else ''}"
            ),
            why=lambda x: (
                f"{x['podiums']} podium{'s' if x['podiums'] != 1 else ''} with {x['wins']} "
                f"win{'s' if x['wins'] != 1 else ''}: {x['podium_points']} points on a 3/2/1 "
                "scale, which breaks the tie when riders have the same count."
            ),
            rule="podiums weighted 3/2/1 for 1st/2nd/3rd",
        )
    )
    out.append(
        _listing(
            "Results",
            "Race winner",
            sorted([x for x in lines if x["wins"]], key=lambda x: -x["wins"]),
            value=lambda x: f"{x['wins']} win{'s' if x['wins'] != 1 else ''}",
            why="Won their category at a scoring race this season.",
            rule="any 1st place in a scoring race",
        )
    )
    out.append(
        _listing(
            "Results",
            "Top-10 machine",
            [x for x in lines if x["races"] >= MIN_RACES and x["top10"] == x["started"]],
            value=lambda x: f"{x['top10']}/{x['started']}",
            why="Top-10 in the category at every start, DNFs included.",
            rule="at least 2 starts, all of them top-10",
        )
    )
    out.append(
        _listing(
            "Results",
            "Top-half club",
            [x for x in lines if x["races"] >= MIN_RACES and x["top_half"] == x["started"]],
            value=lambda x: f"{x['top_half']}/{x['started']}",
            why="Finished in the top half of the category at every start.",
            rule="at least 2 starts, all of them in the top half",
        )
    )
    for label, pool in (
        ("Top points, high school", [x for x in lines if not x["ms"] and x["points"]]),
        ("Top points, middle school", [x for x in lines if x["ms"] and x["points"]]),
    ):
        out.append(
            _single(
                "Results",
                label,
                pool,
                key=lambda x: x["points"],
                value=lambda x: f"{x['points']:,}",
                why=lambda x: (
                    f"{x['points']:,} league points over {x['started']} starts. Points scale "
                    "with the division, so high school and middle school are scored apart."
                ),
                rule="league points, all scoring races",
            )
        )
    out.append(
        _single(
            "Results",
            "Best championship ride",
            [x for x in lines if x["champ_percentile"] is not None],
            key=lambda x: x["champ_percentile"],
            value=_place_of,
            why=lambda x: (
                f"{_place_of(x)} at the state championship, beating "
                f"{_pct(x['champ_percentile'])} of the category."
            ),
            rule="share of the category beaten at the state championship",
        )
    )
    out.append(
        _single(
            "Results",
            "Closest to the front",
            [x for x in lines if x["avg_behind"] is not None and x["behind_races"] >= MIN_RACES],
            key=lambda x: x["avg_behind"],
            reverse=False,
            value=lambda x: f"{x['avg_behind']:.1f}% back",
            why=lambda x: (
                f"Finished {x['avg_behind']:.1f}% behind the category winner's time on average "
                f"over {x['behind_races']} races on the winner's laps."
            ),
            rule="gap to the winner as a share of the winner's time; at least 2 races on the same laps",
        )
    )

    # ── Improvement ──
    improved = [x for x in lines if x["delta_percentile"] is not None and x["races"] >= MIN_RACES]
    same = [x for x in improved if not x["moved_up"] and x["delta_percentile"] > 0]
    stepped = [x for x in improved if x["moved_up"] and x["delta_percentile"] > 0]
    out.append(
        _single(
            "Improvement",
            "Most improved, year over year",
            same,
            key=lambda x: x["delta_percentile"],
            value=lambda x: f"{_signed(x['delta_percentile'])} pts",
            why=lambda x: (
                f"Average share of the category beaten up {x['delta_percentile']:.0f} points on "
                f"last season in the same division, now {_pct(x['avg_percentile'])}."
            ),
            rule="change in average percentile vs last season, same division",
        )
    )
    out.append(
        _single(
            "Improvement",
            "Most improved, step up",
            stepped,
            key=lambda x: x["delta_percentile"],
            value=lambda x: f"{_signed(x['delta_percentile'])} pts",
            why=lambda x: (
                f"Moved up from {x['prev_division']} to {x['division']} and still improved: "
                f"average share beaten up {x['delta_percentile']:.0f} points to "
                f"{_pct(x['avg_percentile'])}."
            ),
            rule="change in average percentile vs last season, after moving up a division",
        )
    )
    out.append(
        _single(
            "Improvement",
            "Strongest finish to the season",
            [x for x in lines if x["climb"] is not None and x["climb"] > 0],
            key=lambda x: x["climb"],
            value=lambda x: f"{_signed(x['climb'])} pts",
            why=lambda x: (
                f"Share of the category beaten rose {x['climb']:.0f} points from the first race "
                f"({_pct(x['percentiles'][0])}) to the last ({_pct(x['percentiles'][-1])})."
            ),
            rule="percentile, last race against the first; at least 3 races",
        )
    )
    climbers = [
        x
        for x in lines
        if len(x["percentiles"]) >= MIN_RACES_TREND
        and all(b >= a for a, b in zip(x["percentiles"], x["percentiles"][1:]))
        and x["percentiles"][-1] > x["percentiles"][0]
    ]
    out.append(
        _single(
            "Improvement",
            "Steady climber",
            climbers,
            key=lambda x: (x["climb"] or 0, x["races"]),
            value=lambda x: " → ".join(_pct(p) for p in x["percentiles"]),
            why=lambda x: "Share of the category beaten went up, or held, at every race.",
            rule="percentile never fell race to race; at least 3 races",
        )
    )

    # ── Engine ──
    out.append(
        _single(
            "Engine",
            "Fastest rider",
            [x for x in lines if x["best_mph"]],
            key=lambda x: x["best_mph"],
            value=lambda x: f"{x['best_mph']:.1f} mph",
            why=lambda x: (
                f"Fastest race average on the team at {x['best_mph']:.1f} mph, course-adjusted "
                f"(loop length and prologue); best single lap {x['best_lap_mph']:.1f} mph."
                if x["best_lap_mph"]
                else f"Fastest race average on the team at {x['best_mph']:.1f} mph, course-adjusted."
            ),
            rule="race distance over riding time; only races whose lap splits add up",
        )
    )
    out.append(
        _single(
            "Engine",
            "Holeshot",
            [x for x in lines if x["best_start"]],
            key=lambda x: (x["holeshots"], -x["best_start"][0], x["best_start"][1]),
            value=lambda x: (
                f"led lap 1 ×{x['holeshots']}"
                if x["holeshots"]
                else f"{_ordinal(x['best_start'][0])} of {x['best_start'][1]} after lap 1"
            ),
            why=lambda x: (
                f"First across the line after lap 1 in {x['holeshots']} "
                f"race{'s' if x['holeshots'] != 1 else ''}."
                if x["holeshots"]
                else (
                    f"Best start on the team: {_ordinal(x['best_start'][0])} of "
                    f"{x['best_start'][1]} after lap 1 at {x['best_start'][2]}."
                )
            ),
            rule="position after lap 1 in the category",
        )
    )
    out.append(
        _single(
            "Engine",
            "Closer",
            [x for x in lines if x["avg_fade"] is not None and x["fade_races"] >= MIN_RACES],
            key=lambda x: x["avg_fade"],
            reverse=False,
            value=lambda x: f"{_signed(x['avg_fade'], 1)}%",
            why=lambda x: (
                f"Last lap against the second lap, averaged over {x['fade_races']} races of 3+ "
                f"laps: {_signed(x['avg_fade'], 1)}%"
                + (" — finished faster than they started." if x["avg_fade"] < 0 else ".")
            ),
            rule="last lap vs lap 2 (lap 1 carries the prologue); at least 2 races of 3+ laps",
        )
    )
    out.append(
        _single(
            "Engine",
            "Comeback ride",
            [x for x in lines if x["comeback"] and x["comeback"]["gain"] > 0],
            key=lambda x: (x["comeback"]["gain"], -x["comeback"]["to"]),
            value=lambda x: f"+{x['comeback']['gain']} places",
            why=lambda x: (
                f"{_ordinal(x['comeback']['from'])} after lap 1 to {_ordinal(x['comeback']['to'])} "
                f"of {x['comeback']['field']} at {x['comeback']['event']}."
            ),
            rule="places gained between lap 1 and the finish, best single race",
        )
    )
    out.append(
        _single(
            "Engine",
            "Diesel",
            [x for x in lines if x["avg_cv"] is not None and x["fade_races"] >= MIN_RACES],
            key=lambda x: x["avg_cv"],
            reverse=False,
            value=lambda x: f"±{x['avg_cv']:.1f}%",
            why=lambda x: (
                f"Steadiest pacing on the team: lap times varied only {x['avg_cv']:.1f}% lap to "
                f"lap (laps 2 onward) over {x['fade_races']} races."
            ),
            rule="lap-to-lap variation of laps 2+, averaged; at least 2 races of 3+ laps",
        )
    )

    # ── Durability ──
    out.append(
        _single(
            "Durability",
            "Iron rider",
            [x for x in lines if x["laps"]],
            key=lambda x: x["laps"],
            ties=True,
            value=lambda x: f"{x['laps']} laps",
            why=lambda x: (
                f"{x['laps']} race laps over {x['started']} starts"
                + (f", about {x['miles']} miles." if x["miles"] else ".")
            ),
            rule="most laps ridden",
        )
    )
    if event_count >= 2:
        out.append(
            _listing(
                "Durability",
                "Perfect finisher",
                [x for x in lines if len(x["events"]) == event_count and not x["dnfs"]],
                value=lambda x: f"{x['started']}/{x['started']}",
                why=f"Started and finished every one of the {event_count} races the team went to.",
                rule="every race started, none unfinished",
            )
        )
    out.append(
        _single(
            "Durability",
            "Most consistent",
            [x for x in lines if x["spread"] is not None],
            key=lambda x: (x["spread"], -(x["avg_percentile"] or 0)),
            reverse=False,
            value=lambda x: f"±{x['spread']:.0f} pts",
            why=lambda x: (
                f"Share of the category beaten stayed within ±{x['spread']:.0f} points of a "
                f"{_pct(x['avg_percentile'])} average over {x['races']} races."
            ),
            rule="spread of percentile across races; at least 3 races",
        )
    )

    # ── Rookie ──
    out.append(
        _single(
            "Rookie",
            "Rookie of the year",
            [x for x in ranked if x["rookie"]],
            key=lambda x: (x["avg_percentile"], x["podiums"]),
            value=lambda x: _pct(x["avg_percentile"]),
            why=lambda x: (
                f"First PICL season: beat {_pct(x['avg_percentile'])} of the category on average "
                f"over {x['races']} races, best place {_ordinal(x['best_place'])}"
                + (f", {_place_of(x)} at the championship." if x["champ_place"] else ".")
            ),
            rule="no results in any earlier season; at least 2 races",
        )
    )

    return [a for a in out if a]


def season_recap(
    rows: list[dict[str, Any]], movers: list[dict[str, Any]], group: str | None = None
) -> dict[str, Any] | None:
    """Everything the recap page shows for one team season; None with no rows.

    `group` narrows the page to "ms" or "hs" (the awards, the rider table and
    the totals are then for that half of the team).
    """
    if not rows:
        return None
    lines = rider_lines(rows, movers)
    if group in GROUPS:
        lines = [x for x in lines if x["ms"] == (group == "ms")]
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
    slate = awards(lines, event_count)
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
        "awards": slate,
        "sections": [
            s
            for s in ("Results", "Improvement", "Engine", "Durability", "Rookie")
            if any(a["section"] == s for a in slate)
        ],  # fmt: skip
        "perfect_attendance": every_race,
        "lines": lines,
        "totals": totals,
        "events": sorted(events.values(), key=lambda e: (e.get("event_order") or 0, e["event_id"])),
        "group": group if group in GROUPS else None,
        "group_label": GROUPS.get(group or ""),
    }
