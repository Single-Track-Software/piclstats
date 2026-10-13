"""Race read — how a rider paced one race, in a sentence and a lap table.

Raw lap-to-lap change misleads: lap 1 often carries a start loop of another
length, and a course can ride faster or slower as the day goes on. So every
lap is read against the *same lap* for the rest of the category that day:

    rel[i] = log(rider's lap i / median lap i of the category's finishers
                 who rode the same number of laps)

and the race's *shape* is how that changed from the first lap to the last
(`delta`): positive = the rider slowed more than the field did. Measured over
2022-26 (12.5k rider-races), the middle half of riders sit within about ±3%;
±4% puts roughly a fifth on each side, so "even" is the common case and the
other two labels mean something.

A rider's *usual* pace comes from the form score (ratings.py), not from rel:
a state race draws a stronger field than a conference race, so "against the
category" would call every state race slow. Each race's laps are shifted onto
the form scale by that day's field level (the race's score less the rider's
average lap against the field's, both on average lap time so a bad lap
doesn't skew the shift) and usual = the median
score of the rider's other races that season, so a lap can also be read
against the rider themself. A
single lap well off their usual pace, and well off their other laps that day,
is the signature of a crash, mechanical, cramp or stop — flagged as a
question for the rider, never as a fact. That fires on about 2% of
rider-races.

Pacing *habits* were checked too: race-to-race correlation of a rider's shape
is only about 0.13 (split-half by season 0.2), so this reads each race on its
own and does not label a rider's tendency.

Pure module (no DB): it takes the category lap rows from
`queries.pacing_lap_rows()` and the rider's own canonical id.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

_LAP_KEYS = ("lap1", "lap2", "lap3", "lap4", "lap5", "lap6")

PACING_CONFIG: dict = {
    # Riders with the same lap count needed to take a per-lap median from.
    "min_field": 8,
    # |delta| at or past this (log units ≈ fraction) leaves "even".
    "shape_threshold": 0.04,
    # A lap this far slower than the rider's usual pace (or, with no usual
    # pace yet, than their other laps that day)...
    "issue_vs_usual": 0.15,
    # ...and this far slower than their other laps the same day.
    "issue_vs_other_laps": 0.12,
    # Every lap at least this far slower than usual: an off day.
    "off_day": 0.10,
    # Other races in the season needed before "usual pace" means anything.
    "min_usual_races": 2,
}


def _laps(row: dict) -> list[float]:
    """Contiguous lap times in seconds; stops at the first missing or bad split."""
    out: list[float] = []
    for key in _LAP_KEYS:
        secs = row.get(key)
        if secs is None or float(secs) <= 0:
            break
        out.append(float(secs))
    return out


def _pct(x: float) -> float:
    """A log ratio as a percent (0.05 → +5.1%)."""
    return round((math.exp(x) - 1) * 100, 1)


def read_race(rider_id: int, rows: list[dict], config: dict | None = None) -> dict | None:
    """One race, one category: the rider's laps against the field's.

    `rows` = every result in the category (rider_id canonical, status, place,
    dq_status, lap1..lap6 in seconds). None when the rider has under two laps
    or too few riders rode the same laps to read the field.
    """
    cfg = {**PACING_CONFIG, **(config or {})}
    mine = next((r for r in rows if r["rider_id"] == rider_id), None)
    if mine is None or mine.get("dq_status") == "excluded":
        return None
    laps = _laps(mine)
    n = len(laps)
    if n < 2:
        return None
    peers = [
        lp
        for r in rows
        if r.get("status") == "OK"
        and r.get("place") is not None
        and r.get("dq_status") != "excluded"
        and len(lp := _laps(r)) == n
    ]
    if len(peers) < cfg["min_field"]:
        return None
    med = [median(p[i] for p in peers) for i in range(n)]
    rel = [math.log(a / b) for a, b in zip(laps, med)]

    # Place after each lap: rank by elapsed time among everyone in the
    # category who completed that lap (the Race Position chart's rule).
    tracks = [lp for r in rows if (lp := _laps(r))]
    lap_rows = []
    elapsed = 0.0
    for i, secs in enumerate(laps):
        elapsed += secs
        done = [sum(t[: i + 1]) for t in tracks if len(t) > i]
        lap_rows.append(
            {
                "lap": i + 1,
                "secs": secs,
                "field_secs": med[i],
                "vs_field_pct": _pct(rel[i]),
                "place": 1 + sum(1 for c in done if c < elapsed),
                "of": len(done),
            }
        )
    return {
        "laps": n,
        "field": len(peers),
        "rel": rel,
        "delta": rel[-1] - rel[0],
        # Average lap against the field's, the way the form score averages.
        "rel_total": math.log(sum(laps) / sum(med)),
        "lap_rows": lap_rows,
    }


def _shape(delta: float, threshold: float) -> str:
    if delta >= threshold:
        return "hard"
    if delta <= -threshold:
        return "strong"
    return "even"


def _issue_lap(rel: list[float], usual: float | None, cfg: dict) -> tuple[int, float] | None:
    """(lap index, how far off) for the one lap that looks like trouble, if any."""
    worst = None
    for i, v in enumerate(rel):
        rest = rel[:i] + rel[i + 1 :]
        if not rest:
            continue
        base = usual if usual is not None else median(rest)
        off = v - base
        if off >= cfg["issue_vs_usual"] and v - median(rest) >= cfg["issue_vs_other_laps"]:
            if worst is None or off > worst[1]:
                worst = (i, off)
    return worst


def _places_phrase(lap_rows: list[dict]) -> str:
    first, last = lap_rows[0]["place"], lap_rows[-1]["place"]
    if last < first:
        moved = f"gained {first - last} place{'s' if first - last != 1 else ''}"
    elif last > first:
        moved = f"lost {last - first} place{'s' if last - first != 1 else ''}"
    else:
        moved = "held position"
    trail = " → ".join(str(r["place"]) for r in lap_rows)
    return f"Place after each lap: {trail} ({moved} from lap 1 to the finish)."


def summarize(read: dict, usual: float | None, config: dict | None = None) -> dict:
    """Label + plain-English sentences for one race read (see read_race)."""
    cfg = {**PACING_CONFIG, **(config or {})}
    rel, n = read["rel"], read["laps"]
    shape = _shape(read["delta"], cfg["shape_threshold"])
    issue = _issue_lap(rel, usual, cfg)
    off_day = usual is not None and issue is None and min(rel) - usual >= cfg["off_day"]
    gap = abs(_pct(read["delta"]))

    lines: list[str] = []
    if issue is not None:
        i, off = issue
        label = "issue"
        against = "your usual pace" if usual is not None else "your other laps"
        if i == 0:
            lines.append(
                f"Lap 1 was {_pct(off):.0f}% slower than {against} — more than start "
                "traffic usually costs. An early crash or mechanical, or a back-row start?"
            )
        else:
            lines.append(
                f"Lap {i + 1} was {_pct(off):.0f}% slower than {against} — more than a "
                "normal fade. A crash, mechanical or cramp, or just ran out of energy?"
            )
    elif off_day:
        label = "off"
        lines.append(
            f"Every lap was at least {_pct(min(rel) - usual):.0f}% slower than your "
            "usual pace this season, against the same field — an off day "
            "(tired, unwell, an early spill)?"
        )
    else:
        label = shape
    if shape == "hard" and issue is None:
        lines.append(
            f"Went out hard: from lap 1 to lap {n} you slowed {gap:.1f}% more than the field did."
        )
    elif shape == "strong" and issue is None:
        lines.append(
            f"Finished strong: from lap 1 to lap {n} you held pace {gap:.1f}% better than the field."
        )
        if read["lap_rows"][0]["vs_field_pct"] > 0:
            lines.append("A slow lap 1 can also be traffic from a back-row start.")
    elif issue is None:
        lines.append(f"Even pacing: lap to lap you kept the field's rhythm (within {gap:.1f}%).")
    lines.append(_places_phrase(read["lap_rows"]))
    if usual is not None:
        # With a flagged lap, judge the rest of the race without it.
        clean = [v for i, v in enumerate(rel) if issue is None or i != issue[0]]
        vs_usual = _pct(median(clean) - usual)
        lead = f"Apart from lap {issue[0] + 1}, " if issue else ""
        lines.append(
            f"{lead}{'o' if lead else 'O'}verall {abs(vs_usual):.1f}% "
            f"{'faster' if vs_usual < 0 else 'slower'} than your usual pace this season."
            if abs(vs_usual) >= 1
            else f"{lead}{'r' if lead else 'R'}ight on your usual pace this season."
        )
    return {
        "label": label,
        "delta_pct": _pct(read["delta"]),
        "lines": lines,
        "lap_rows": read["lap_rows"],
        "field": read["field"],
        "issue_lap": issue[0] + 1 if issue else None,
    }


def rider_pacing(
    rider_id: int,
    races: list[dict],
    lap_rows: list[dict],
    scores: dict[int, float] | None = None,
    config: dict | None = None,
) -> dict[tuple[int, str], dict]:
    """Pacing summary per (event_id, category) for one rider.

    `races` = the rider's race history (event_id, category, season);
    `lap_rows` = `queries.pacing_lap_rows()` for those events and categories;
    `scores` = form score per event_id in log units (ratings.Score.x). A race
    without one still gets its shape and places, but no usual-pace reading.
    """
    scores = scores or {}
    by_race: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in lap_rows:
        by_race[(r["event_id"], r["category"])].append(r)
    reads: dict[tuple[int, str], tuple[int, dict]] = {}
    for race in races:
        key = (race["event_id"], race["category"])
        if key in reads or key not in by_race:
            continue
        read = read_race(rider_id, by_race[key], config)
        if read is not None:
            reads[key] = (race["season"], read)

    cfg = {**PACING_CONFIG, **(config or {})}
    out: dict[tuple[int, str], dict] = {}
    for key, (season, read) in reads.items():
        x = scores.get(key[0])
        others = [
            scores[k[0]]
            for k, (s, _) in reads.items()
            if s == season and k != key and k[0] in scores
        ]
        usual = None
        if x is not None and len(others) >= cfg["min_usual_races"]:
            # Onto the form scale: same lap shape, shifted by that day's field level.
            shift = x - read["rel_total"]
            read = {**read, "rel": [v + shift for v in read["rel"]]}
            usual = median(others)
        out[key] = summarize(read, usual, config)
    return out
