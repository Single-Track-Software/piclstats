"""Race-position bump chart — position-by-lap for one event + category.

Reproduces the CrossMgr "Race Position" chart coaches use to read race shape:
x-axis = lap number, y-axis = position (1 at top). Each rider is a line; where
a rider's line ends early they were lapped/pulled/DNF'd — the same visual cue as
CrossMgr's dashed tail.

The only computation is cumulative-time-then-rank: a rider's position at lap k is
their rank by elapsed time among everyone who has *completed* lap k. Riders who
finished fewer laps than the category leader simply have no position past their
last completed lap, so their line stops there.

Pure module (no DB/IO): it takes the per-rider lap rows produced by
queries.event_lap_rows() and builds a Chart.js-ready view model.
"""

from __future__ import annotations

from dataclasses import dataclass

_LAP_KEYS = ("lap1", "lap2", "lap3", "lap4", "lap5", "lap6")

# A small, high-contrast palette cycled across riders. Lead riders (drawn last)
# read clearest; the table beneath the chart is the authoritative key.
_PALETTE = [
    "#1020e8", "#e8590c", "#2f9e44", "#c2255c", "#7048e8", "#0c8599",
    "#f08c00", "#495057", "#1864ab", "#a61e4d", "#5c940d", "#862e9c",
    "#e03131", "#1098ad", "#d9480f", "#364fc7", "#2b8a3e", "#9c36b5",
]


@dataclass(frozen=True)
class RiderTrack:
    """One rider's line on the bump chart."""

    bib: int
    name: str
    team: str | None
    color: str
    positions: list[int | None]   # position at lap 1..N (None once they stop)
    laps_done: int                # number of laps actually completed
    finish_place: int | None      # official place from results, if any
    status: str                   # OK | DNF | DNS | DSQ
    complete: bool                # finished the full category distance


def _cumulative_seconds(row: dict) -> list[float]:
    """Elapsed time at the end of each completed lap.

    Stops at the first missing split — a rider lapped out after lap 3 has no
    lap-4 time, and anything recorded beyond a gap can't be trusted as a
    contiguous race time.
    """
    cum: list[float] = []
    running = 0.0
    for key in _LAP_KEYS:
        secs = row.get(key)
        if secs is None:
            break
        running += float(secs)
        cum.append(running)
    return cum


def build_position_chart(rows: list[dict]) -> dict:
    """Build the bump-chart view model from per-rider lap rows.

    Each row needs: bib, name, team, status, place, and lap1..lap6 in *seconds*
    (None where the rider has no split for that lap). Returns a dict with the
    rider tracks, lap labels, and field/finisher counts for the template.
    """
    riders = []
    for r in rows:
        cum = _cumulative_seconds(r)
        if not cum:
            continue  # DNS / no timing — nothing to plot
        riders.append({"row": r, "cum": cum, "laps_done": len(cum)})

    if not riders:
        return {"tracks": [], "n_laps": 0, "lap_labels": [], "field": 0, "finishers": 0}

    n_laps = max(r["laps_done"] for r in riders)

    # Position at each lap = rank by elapsed time among riders who completed it.
    # ranks[lap_index][bib] = position
    ranks: list[dict[int, int]] = []
    for lap in range(n_laps):
        contenders = [r for r in riders if r["laps_done"] > lap]
        contenders.sort(key=lambda r: r["cum"][lap])
        ranks.append({id(r): pos for pos, r in enumerate(contenders, start=1)})

    tracks: list[RiderTrack] = []
    for i, r in enumerate(sorted(riders, key=lambda r: (r["laps_done"], -r["cum"][-1]), reverse=True)):
        row = r["row"]
        positions = [ranks[lap].get(id(r)) for lap in range(n_laps)]
        complete = r["laps_done"] == n_laps and (row.get("status") or "OK") == "OK"
        tracks.append(RiderTrack(
            bib=row["bib"],
            name=row.get("name") or f"#{row['bib']}",
            team=row.get("team"),
            color=_PALETTE[i % len(_PALETTE)],
            positions=positions,
            laps_done=r["laps_done"],
            finish_place=row.get("place"),
            status=row.get("status") or "OK",
            complete=complete,
        ))

    # Order tracks by finishing position (final-lap rank, then official place) so
    # the table and legend read top-to-bottom like a results sheet.
    def _final_rank(t: RiderTrack) -> tuple:
        last = next((p for p in reversed(t.positions) if p is not None), 10_000)
        return (not t.complete, last, t.finish_place or 10_000)

    tracks.sort(key=_final_rank)

    return {
        "tracks": tracks,
        "n_laps": n_laps,
        "lap_labels": [f"Lap {i + 1}" for i in range(n_laps)],
        "field": len(riders),
        "finishers": sum(1 for t in tracks if t.complete),
    }
