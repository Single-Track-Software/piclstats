"""League page analysis: conferences head to head, and riders over time.

Conferences only meet at mixed-conference races (the state events), so the
comparison uses those alone; queries.cross_conference_performance supplies
the per-conference totals and this module turns them into shares, a lead
sentence and a division table. riders_trend shapes the per-season counts
for the stacked bars.
"""

from __future__ import annotations

from typing import Any

from piclstats.web.staging import division_sort_key

# A division cell needs this many results before its average means much.
MIN_DIVISION_RESULTS = 10


def _order(names: set[str], preferred: list[str]) -> list[str]:
    return [c for c in preferred if c in names] + sorted(names - set(preferred))


def conference_comparison(perf: dict[str, Any], conferences: list[str]) -> dict[str, Any]:
    """Rows west to east with field/top-10 shares, plus a one-line finding."""
    rows = [r for r in perf["conferences"] if r.get("conference")]
    total = sum(r["results"] for r in rows)
    top10 = sum(r["top10"] for r in rows)
    order = _order({r["conference"] for r in rows}, conferences)
    by_name = {r["conference"]: r for r in rows}
    out = []
    for name in order:
        r = by_name[name]
        field_share = 100 * r["results"] / total if total else 0.0
        top10_share = 100 * r["top10"] / top10 if top10 else 0.0
        out.append(
            {
                **r,
                "field_share": round(field_share, 1),
                "top10_share": round(top10_share, 1),
                "top10_gap": round(top10_share - field_share, 1),
            }
        )
    finding = None
    if len(out) >= 2 and top10:
        best = max(out, key=lambda r: r["top10_gap"])
        worst = min(out, key=lambda r: r["top10_gap"])
        finding = (
            f"{best['conference']} riders took {best['top10_share']:.0f}% of top-10 finishes "
            f"from {best['field_share']:.0f}% of the field; "
            f"{worst['conference']} took {worst['top10_share']:.0f}% "
            f"from {worst['field_share']:.0f}%."
        )
    return {"events": perf["events"], "rows": out, "finding": finding}


def division_comparison(perf: dict[str, Any], conferences: list[str]) -> dict[str, Any]:
    """Share of the field beaten per division (oldest first) and conference.

    Cells with fewer than MIN_DIVISION_RESULTS results are None; `best`
    names the strongest conference in each division among the rest.
    """
    cells = [r for r in perf["by_division"] if r.get("conference") and r.get("division")]
    confs = _order({r["conference"] for r in cells}, conferences)
    divisions = sorted({r["division"] for r in cells}, key=division_sort_key)
    table = []
    for d in divisions:
        values: dict[str, float | None] = {}
        for c in confs:
            hit = next((r for r in cells if r["division"] == d and r["conference"] == c), None)
            ok = hit is not None and hit["results"] >= MIN_DIVISION_RESULTS
            values[c] = float(hit["avg_beaten"]) if ok and hit else None
        scored = {c: v for c, v in values.items() if v is not None}
        best = max(scored, key=lambda c: scored[c]) if len(scored) >= 2 else None
        table.append({"division": d, "values": values, "best": best})
    return {"conferences": confs, "rows": table}


def riders_trend(rows: list[dict[str, Any]], conferences: list[str]) -> dict[str, Any]:
    """Stacked-bar data: seasons oldest first, a series per conference
    (west to east), "All riders" where no conference is on record."""
    seasons = sorted({r["season"] for r in rows})
    labelled = [
        {
            **r,
            "conference": r["conference"]
            or (
                "Unassigned"
                if any(x["season"] == r["season"] and x["conference"] for x in rows)
                else "All riders"
            ),
        }
        for r in rows
    ]
    names = {r["conference"] for r in labelled}
    lead = [n for n in ("All riders",) if n in names]
    tail = [n for n in ("Unassigned",) if n in names]
    series = lead + _order(names - set(lead) - set(tail), conferences) + tail
    counts = {s: [0] * len(seasons) for s in series}
    for r in labelled:
        counts[r["conference"]][seasons.index(r["season"])] += r["riders"]
    totals = [sum(counts[s][i] for s in series) for i in range(len(seasons))]
    return {"seasons": seasons, "series": series, "counts": counts, "totals": totals}
