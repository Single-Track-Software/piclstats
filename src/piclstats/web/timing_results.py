"""Rally timing — turning crossings into segment times, flags and results (ADR 005).

Pure module: no DB or IO. The lead's results page feeds it the segments, the
effective crossings (originals with their latest correction applied), the
roster, penalties and accepted flags, and gets back a rider-by-segment
table, the flag list, and the publishable results.

Rules (host guide and requirements): a rider's total is the sum of the
segments their group (HS or MS, from the roster category) rides; a rider
missing a segment their group rides is a DNF; each segment is ranked on its
own; scoring places riders within their category by total time; penalties
add to the total.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from piclstats.db.seed import DIVISION_PROFILES

# ── Inputs ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Segment:
    id: int
    seq: int
    name: str
    start_point_id: int
    finish_point_id: int
    rides_hs: bool = True
    rides_ms: bool = True
    distance_miles: float | None = None


@dataclass(frozen=True)
class Crossing:
    """One effective crossing: an original with its latest correction applied."""

    id: str
    point_id: int
    ts: datetime
    plate: int | None
    voided: bool = False
    note: str | None = None
    kind: str = "tap"
    device_id: str | None = None


@dataclass(frozen=True)
class RosterRider:
    plate: int
    name: str
    team: str | None
    category: str | None


@dataclass(frozen=True)
class Adjustment:
    plate: int
    seconds: float
    reason: str
    segment_id: int | None = None


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    label: str | None
    point_id: int
    pending: int
    drift_ms: int
    last_seen: datetime | None


# ── Outputs ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Flag:
    """Something the lead must look at. `key` is stable so an override sticks."""

    key: str
    kind: str
    severity: str  # 'block' (must resolve or accept) | 'info'
    plate: int | None
    segment_id: int | None
    point_id: int | None
    crossing_id: str | None
    text: str
    accepted: bool = False


@dataclass
class SegmentTime:
    segment_id: int
    start: Crossing | None
    finish: Crossing | None
    seconds: float | None  # finish − start, before penalties
    rank: int | None = None  # within category, this segment


@dataclass
class RiderResult:
    plate: int
    name: str
    team: str | None
    category: str | None
    division: str | None
    gender: str | None
    group: str  # 'HS' | 'MS'
    segments: dict[int, SegmentTime] = field(default_factory=dict)  # by segment id
    penalty_seconds: float = 0.0
    adjustments: list[Adjustment] = field(default_factory=list)
    status: str = "OK"  # 'OK' | 'DNF' | 'DNS'
    total_seconds: float | None = None  # sum of ridden segments + penalty
    place: int | None = None

    @property
    def ridden_segment_ids(self) -> list[int]:
        return list(self.segments.keys())


@dataclass
class Reconciliation:
    riders: list[RiderResult]
    flags: list[Flag]
    unassigned: list[Crossing]  # non-voided crossings with no plate

    @property
    def blocking(self) -> list[Flag]:
        return [f for f in self.flags if f.severity == "block" and not f.accepted]


# ── Helpers ────────────────────────────────────────────────────────────────

_LOOP_BY_DIVISION: dict[str, str] = {}
for _div, _gender, _laps, _max, _cut, _loop in DIVISION_PROFILES:
    _LOOP_BY_DIVISION.setdefault(_div, _loop)


def split_category(category: str | None) -> tuple[str | None, str | None]:
    """'JV1 - Male' -> ('JV1', 'Male'); 'Varsity' -> ('Varsity', None)."""
    if not category:
        return None, None
    cat = category.strip()
    gender = None
    for suffix, g in (
        (" - Male", "Male"),
        (" - Boys", "Male"),
        (" - Female", "Female"),
        (" - Girls", "Female"),
    ):
        if cat.endswith(suffix):
            gender = g
            cat = cat[: -len(suffix)]
            break
    return cat.strip() or None, gender


def group_for_category(category: str | None) -> tuple[str, bool]:
    """('HS' | 'MS', known): which segment set a rider in this category rides.

    Uses the same division-to-loop mapping as the course profiles. An unknown
    category is treated as HS (every segment) and reported so the lead sees it.
    """
    division, _ = split_category(category)
    if division is None:
        return "HS", False
    loop = _LOOP_BY_DIVISION.get(division)
    if loop is None:
        for known, lp in _LOOP_BY_DIVISION.items():
            if known.lower() == division.lower():
                loop = lp
                break
    if loop is None:
        return "HS", False
    return loop, True


def format_seconds(seconds: float | None) -> str:
    """32:18.8 or 1:02:33.4, the way raceresult totals are stored."""
    if seconds is None:
        return ""
    tenths = int(round(seconds * 10))
    s, t = divmod(tenths, 10)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}.{t}" if h else f"{m}:{sec:02d}.{t}"


def effective_crossings(rows: list[dict]) -> list[Crossing]:
    """Fold correction chains: each original with its latest correction's values.

    `rows` are timing_crossings rows as dicts (id, point_id, ts, plate, kind,
    supersedes, voided, note, received_at, device_id). A correction's
    `supersedes` may point at an original or at an earlier correction; the
    chain is followed to its root, and the correction received last wins.
    """
    by_id = {r["id"]: r for r in rows}

    def root_of(r: dict) -> str:
        seen = set()
        cur = r
        while cur.get("supersedes") and cur["supersedes"] in by_id and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = by_id[cur["supersedes"]]
        return cur["id"]

    latest: dict[str, dict] = {}
    for r in rows:
        if r["kind"] != "correction":
            continue
        root = root_of(r)
        cur = latest.get(root)
        if cur is None or (r.get("received_at"), r["id"]) > (cur.get("received_at"), cur["id"]):
            latest[root] = r

    out: list[Crossing] = []
    for r in rows:
        if r["kind"] == "correction":
            continue
        c = latest.get(r["id"])
        src = c if c is not None else r
        out.append(
            Crossing(
                id=r["id"],
                point_id=r["point_id"],
                ts=r["ts"],
                plate=src.get("plate"),
                voided=bool(src.get("voided")),
                note=src.get("note"),
                kind=r["kind"],
                device_id=r.get("device_id"),
            )
        )
    out.sort(key=lambda c: (c.ts, c.id))
    return out


# ── The reconciliation ─────────────────────────────────────────────────────

# Segment time sanity, per mile when the distance is known, else absolute.
_MIN_SEC_PER_MILE = 60.0  # 60 mph downhill on a bike is not a rally
_MAX_SEC_PER_MILE = 40 * 60.0
_MIN_SEC = 15.0
_MAX_SEC = 3600.0


def _lookup(point_seg: dict[int, tuple[Segment, str]], point_id: int) -> tuple[Segment | None, str]:
    hit = point_seg.get(point_id)
    return (hit[0], hit[1]) if hit else (None, "?")


def reconcile(
    segments: list[Segment],
    crossings: list[Crossing],
    roster: list[RosterRider],
    adjustments: Sequence[Adjustment] = (),
    accepted_keys: set[str] | frozenset[str] = frozenset(),
    devices: Sequence[DeviceStatus] = (),
    drift_limit_ms: int = 1000,
    tz: tzinfo | None = None,
) -> Reconciliation:
    """Pair starts with finishes per rider and segment; rank, place, and flag."""
    segments = sorted(segments, key=lambda s: s.seq)
    roster_by_plate = {r.plate: r for r in roster}
    point_seg: dict[int, tuple[Segment, str]] = {}
    for sg in segments:
        point_seg[sg.start_point_id] = (sg, "start")
        point_seg[sg.finish_point_id] = (sg, "finish")

    flags: list[Flag] = []

    def clock(ts: datetime) -> str:
        return (ts.astimezone(tz) if tz else ts).strftime("%H:%M:%S")

    def flag(
        kind: str,
        severity: str,
        text: str,
        *,
        plate=None,
        segment_id=None,
        point_id=None,
        crossing_id=None,
        key_extra: str = "",
    ) -> None:
        key = f"{kind}:{plate if plate is not None else '-'}:{segment_id if segment_id is not None else '-'}:{crossing_id or key_extra}"
        flags.append(
            Flag(
                key,
                kind,
                severity,
                plate,
                segment_id,
                point_id,
                crossing_id,
                text,
                accepted=key in accepted_keys,
            )
        )

    live = [c for c in crossings if not c.voided]
    unassigned = [c for c in live if c.plate is None]
    for c in unassigned:
        psg, kind = _lookup(point_seg, c.point_id)
        flag(
            "no_plate",
            "block",
            f"A {kind} crossing at {clock(c.ts)} on segment {psg.seq if psg else '?'} has no plate",
            segment_id=psg.id if psg else None,
            point_id=c.point_id,
            crossing_id=c.id,
        )

    # Crossings per (plate, point)
    by_plate_point: dict[tuple[int, int], list[Crossing]] = {}
    for c in live:
        if c.plate is None:
            continue
        by_plate_point.setdefault((c.plate, c.point_id), []).append(c)
        if c.plate not in roster_by_plate:
            psg, kind = _lookup(point_seg, c.point_id)
            flag(
                "unknown_plate",
                "block",
                f"Plate {c.plate} is not on the roster ({kind} of segment {psg.seq if psg else '?'} at {clock(c.ts)})",
                plate=c.plate,
                segment_id=psg.id if psg else None,
                point_id=c.point_id,
                crossing_id=c.id,
            )

    # Riders: everyone on the roster, plus unknown plates that were recorded.
    plates = sorted(set(roster_by_plate) | {p for p, _ in by_plate_point})
    riders: list[RiderResult] = []
    for plate in plates:
        r = roster_by_plate.get(plate)
        category = r.category if r else None
        division, gender = split_category(category)
        group, known = group_for_category(category)
        if r and not known:
            flag(
                "unknown_category",
                "info",
                f"Plate {plate} ({r.name}) has category {category!r}, not a known division; treated as HS",
                plate=plate,
            )
        known_rider = r is not None  # pairing flags are noise for a plate that is not on the roster
        rider = RiderResult(
            plate=plate,
            name=r.name if r else f"Plate {plate}",
            team=r.team if r else None,
            category=category,
            division=division,
            gender=gender,
            group=group,
        )
        ridden = [sg for sg in segments if (sg.rides_hs if group == "HS" else sg.rides_ms)]
        any_time = False
        for sg in ridden:
            starts = by_plate_point.get((plate, sg.start_point_id), [])
            finishes = by_plate_point.get((plate, sg.finish_point_id), [])
            if len(starts) > 1:
                flag(
                    "duplicate",
                    "block",
                    f"Plate {plate} has {len(starts)} starts on segment {sg.seq}; void the wrong one",
                    plate=plate,
                    segment_id=sg.id,
                    point_id=sg.start_point_id,
                )
            if len(finishes) > 1:
                flag(
                    "duplicate",
                    "block",
                    f"Plate {plate} has {len(finishes)} finishes on segment {sg.seq}; void the wrong one",
                    plate=plate,
                    segment_id=sg.id,
                    point_id=sg.finish_point_id,
                )
            start = starts[0] if len(starts) == 1 else None
            finish = finishes[0] if len(finishes) == 1 else None
            seconds = None
            if start and finish:
                seconds = (finish.ts - start.ts).total_seconds()
                lo, hi = (_MIN_SEC, _MAX_SEC)
                if sg.distance_miles:
                    lo, hi = (
                        max(lo, _MIN_SEC_PER_MILE * sg.distance_miles),
                        min(hi, _MAX_SEC_PER_MILE * sg.distance_miles),
                    )
                if seconds < 0:
                    flag(
                        "implausible",
                        "block",
                        f"Plate {plate} finished segment {sg.seq} before starting it ({seconds:.1f}s); a clock or a wrong plate",
                        plate=plate,
                        segment_id=sg.id,
                    )
                    seconds = None
                elif seconds < lo or seconds > hi:
                    flag(
                        "implausible",
                        "block",
                        f"Plate {plate} took {format_seconds(seconds)} on segment {sg.seq}, outside {format_seconds(lo)}–{format_seconds(hi)}",
                        plate=plate,
                        segment_id=sg.id,
                    )
            elif start and not finishes and known_rider:
                flag(
                    "start_no_finish",
                    "block",
                    f"Plate {plate} started segment {sg.seq} but no finish was recorded",
                    plate=plate,
                    segment_id=sg.id,
                    point_id=sg.finish_point_id,
                )
            elif finish and not starts and known_rider:
                flag(
                    "finish_no_start",
                    "block",
                    f"Plate {plate} finished segment {sg.seq} but no start was recorded",
                    plate=plate,
                    segment_id=sg.id,
                    point_id=sg.start_point_id,
                )
            if starts or finishes:
                any_time = True
            rider.segments[sg.id] = SegmentTime(sg.id, start, finish, seconds)
        # Crossings on segments the rider's group does not ride are still worth a look.
        for sg in segments:
            if sg in ridden:
                continue
            if by_plate_point.get((plate, sg.start_point_id)) or by_plate_point.get(
                (plate, sg.finish_point_id)
            ):
                flag(
                    "not_their_segment",
                    "info",
                    f"Plate {plate} ({rider.category or 'no category'}, {group}) was recorded on segment {sg.seq}, which {group} does not ride",
                    plate=plate,
                    segment_id=sg.id,
                )

        for adj in adjustments:
            if adj.plate == plate:
                rider.adjustments.append(adj)
                rider.penalty_seconds += adj.seconds

        times = [st.seconds for st in rider.segments.values()]
        if not any_time and not rider.segments:
            rider.status = "DNS"
        elif not any_time:
            rider.status = "DNS"
        elif all(t is not None for t in times) and times:
            rider.status = "OK"
            rider.total_seconds = sum(t for t in times if t is not None) + rider.penalty_seconds
        else:
            rider.status = "DNF"
            missing = [str(sg.seq) for sg in ridden if rider.segments[sg.id].seconds is None]
            if known_rider:
                flag(
                    "dnf",
                    "info",
                    f"Plate {plate} ({rider.name}) has no time for segment{'s' if len(missing) > 1 else ''} {', '.join(missing)}: DNF",
                    plate=plate,
                )
        riders.append(rider)

    # Ranks per segment and places per category (OK riders only).
    for sg in segments:
        ranked: list[RiderResult] = sorted(
            (
                rr
                for rr in riders
                if rr.status == "OK"
                and rr.segments.get(sg.id)
                and rr.segments[sg.id].seconds is not None
            ),
            key=lambda rr: rr.segments[sg.id].seconds or 0.0,
        )
        by_cat: dict[str | None, int] = {}
        for rr in ranked:
            by_cat[rr.category] = by_cat.get(rr.category, 0) + 1
            rr.segments[sg.id].rank = by_cat[rr.category]
    by_cat_place: dict[str | None, int] = {}
    finishers: list[RiderResult] = [rr for rr in riders if rr.status == "OK"]
    for rr in sorted(finishers, key=lambda rr: rr.total_seconds or 0.0):
        by_cat_place[rr.category] = by_cat_place.get(rr.category, 0) + 1
        rr.place = by_cat_place[rr.category]

    for d in devices:
        psg, kind = _lookup(point_seg, d.point_id)
        who = d.label or d.device_id[:8]
        if d.drift_ms > drift_limit_ms:
            flag(
                "clock_drift",
                "block",
                f"{who} ({kind} of segment {psg.seq if psg else '?'}): clock moved {d.drift_ms / 1000:.1f}s between syncs; its times may be off",
                segment_id=psg.id if psg else None,
                point_id=d.point_id,
                key_extra=d.device_id,
            )
        if d.pending:
            flag(
                "unsynced",
                "info",
                f"{who} ({kind} of segment {psg.seq if psg else '?'}) still has {d.pending} record{'s' if d.pending != 1 else ''} not synced",
                segment_id=psg.id if psg else None,
                point_id=d.point_id,
                key_extra=d.device_id,
            )

    riders.sort(
        key=lambda r: (r.category or "~", r.status != "OK", r.place or 10_000, r.status, r.plate)
    )
    return Reconciliation(riders=riders, flags=flags, unassigned=unassigned)


def csv_rows(rec: Reconciliation, segments: list[Segment]) -> list[list[str]]:
    """Rows for PICL's scoring: header then one row per rider on the roster or recorded."""
    segs = sorted(segments, key=lambda s: s.seq)
    header = (
        ["plate", "name", "team", "category", "place", "status"]
        + [f"segment_{s.seq}" for s in segs]
        + ["penalty_seconds", "total", "total_seconds"]
    )
    rows = [header]
    for r in rec.riders:
        cells = [str(r.plate), r.name, r.team or "", r.category or "", str(r.place or ""), r.status]
        for s in segs:
            st = r.segments.get(s.id)
            cells.append(format_seconds(st.seconds) if st and st.seconds is not None else "")
        cells += [
            f"{r.penalty_seconds:g}" if r.penalty_seconds else "",
            format_seconds(r.total_seconds),
            f"{r.total_seconds:.1f}" if r.total_seconds is not None else "",
        ]
        rows.append(cells)
    return rows
