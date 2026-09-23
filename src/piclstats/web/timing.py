"""Rally timing — the timing lead's setup pages (ADR 005).

A rally is timed across segments, each with a start and a finish point. The
lead creates the event and its segments here; every point gets a station
code that volunteers join by scanning a printed QR code, so a phone needs no
account. The roster of plates is pasted in, pulled from this season's race
results (plates are stable across a season), or added one at a time.

Station pages, sync, reconciliation and publishing follow in later changes.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from piclstats.db.engine import get_session, rowcount
from piclstats.web.auth import build_link, require_picl, require_same_origin
from piclstats.web.templating import Jinja2Templates

from pathlib import Path

router = APIRouter(prefix="/admin/timing", tags=["timing"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STATUSES = ("setup", "live", "approved", "published")
POINT_KINDS = ("start", "finish")

# Station codes: 8 characters from an alphabet without 0/O/1/I, so a code read
# off a printed sheet or spoken over a radio is never ambiguous.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def new_station_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def station_path(code: str) -> str:
    """Where a station's QR code sends the volunteer."""
    return f"/timing/s/{code}"


# ── Roster parsing ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RosterRow:
    plate: int
    name: str
    team: str | None
    category: str | None


_SPLIT = re.compile(r"\s*[,\t|]\s*")
_HEADER_WORDS = {
    "plate",
    "plate number",
    "plate #",
    "bib",
    "bib number",
    "#",
    "no",
    "no.",
    "number",
}


def parse_roster_lines(raw: str) -> list[RosterRow]:
    """Parse pasted roster lines: plate, name, team, category (comma, tab or | separated).

    Blank lines are skipped and so is a header line (first field "plate",
    "bib" or the like). Team and category are optional. Raises ValueError listing every
    bad line by number, and on a plate given twice.
    """
    rows: list[RosterRow] = []
    errors: list[str] = []
    seen: dict[int, int] = {}
    for n, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [p.strip() for p in _SPLIT.split(line.strip())]
        if n == 1 and parts[0].lower() in _HEADER_WORDS:
            continue  # header
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1]:
            errors.append(f"Line {n}: expected plate, name[, team[, category]]")
            continue
        plate = int(parts[0])
        if plate in seen:
            errors.append(f"Line {n}: plate {plate} already given on line {seen[plate]}")
            continue
        seen[plate] = n
        rows.append(
            RosterRow(
                plate=plate,
                name=parts[1],
                team=parts[2] or None if len(parts) > 2 else None,
                category=parts[3] or None if len(parts) > 3 else None,
            )
        )
    if errors:
        raise ValueError("; ".join(errors))
    return rows


# ── Helpers ────────────────────────────────────────────────────────────────


def _form_str(form: FormData, key: str) -> str:
    value = form.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _redirect(event_id: int, **params: str) -> RedirectResponse:
    query = "".join(f"&{k}={v}" for k, v in params.items())
    return RedirectResponse(f"/admin/timing/{event_id}?_=1{query}", status_code=303)


def _load_event(s: Session, event_id: int) -> dict[str, Any]:
    row = (
        s.execute(
            text("""
            SELECT t.*, c.name AS course
            FROM timing_events t LEFT JOIN courses c ON c.id = t.course_id
            WHERE t.id = :id
        """),
            {"id": event_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Timing event not found")
    return dict(row)


def _segments(s: Session, event_id: int) -> list[dict[str, Any]]:
    """Segments in order, each with its distance, who rides it, and its start and finish point."""
    rows = s.execute(
        text("""
        SELECT sg.id, sg.seq, sg.name, sg.distance_miles, sg.elevation_ft,
               sg.rides_hs, sg.rides_ms, p.id AS point_id, p.kind, p.station_code
        FROM timing_segments sg
        LEFT JOIN timing_points p ON p.segment_id = sg.id
        WHERE sg.timing_event_id = :eid
        ORDER BY sg.seq, p.kind DESC
    """),
        {"eid": event_id},
    ).all()
    segments: dict[int, dict[str, Any]] = {}
    for sid, seq, name, dist, elev, hs, ms, point_id, kind, code in rows:
        seg = segments.setdefault(
            sid,
            {
                "id": sid,
                "seq": seq,
                "name": name,
                "distance_miles": dist,
                "elevation_ft": elev,
                "rides_hs": hs,
                "rides_ms": ms,
                "points": {},
            },
        )
        if point_id is not None:
            seg["points"][kind] = {"id": point_id, "code": code}
    return list(segments.values())


@dataclass(frozen=True)
class SegmentForm:
    name: str
    distance_miles: float | None
    elevation_ft: float | None
    rides_hs: bool
    rides_ms: bool


def parse_segment_form(form: Mapping[str, str]) -> SegmentForm:
    """Parse a segment's fields. Raises ValueError on a missing name, a bad number, or no group."""
    name = form.get("name", "").strip()
    if not name:
        raise ValueError("Segment name is required")

    def opt_float(key: str) -> float | None:
        raw = form.get(key, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{key.replace('_', ' ')} must be a number") from None
        if value < 0:
            raise ValueError(f"{key.replace('_', ' ')} cannot be negative")
        return value

    rides_hs = form.get("rides_hs") is not None
    rides_ms = form.get("rides_ms") is not None
    if not (rides_hs or rides_ms):
        raise ValueError("A segment must be ridden by HS, MS, or both")
    return SegmentForm(
        name=name,
        distance_miles=opt_float("distance_miles"),
        elevation_ft=opt_float("elevation_ft"),
        rides_hs=rides_hs,
        rides_ms=rides_ms,
    )


def _next_seq(s: Session, event_id: int) -> int:
    return (
        s.execute(
            text(
                "SELECT COALESCE(max(seq), 0) + 1 FROM timing_segments WHERE timing_event_id = :e"
            ),
            {"e": event_id},
        ).scalar()
        or 1
    )


def _add_segment(s: Session, event_id: int, seg: SegmentForm) -> int:
    """Create a segment with a start and a finish point, each with a fresh code."""
    seg_id = s.execute(
        text("""
        INSERT INTO timing_segments (timing_event_id, seq, name, distance_miles, elevation_ft,
            rides_hs, rides_ms)
        VALUES (:e, :seq, :name, :dist, :elev, :hs, :ms) RETURNING id
    """),
        {
            "e": event_id,
            "seq": _next_seq(s, event_id),
            "name": seg.name,
            "dist": seg.distance_miles,
            "elev": seg.elevation_ft,
            "hs": seg.rides_hs,
            "ms": seg.rides_ms,
        },
    ).scalar_one()
    for kind in POINT_KINDS:
        s.execute(
            text("""
            INSERT INTO timing_points (segment_id, kind, station_code)
            VALUES (:sid, :kind, :code)
        """),
            {"sid": seg_id, "kind": kind, "code": new_station_code()},
        )
    return seg_id


# ── Pages ──────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def timing_index(
    request: Request, saved: str = "", error: str = "", user: dict = Depends(require_picl)
):
    with get_session() as s:
        events = (
            s.execute(
                text("""
                SELECT t.id, t.season, t.name, t.event_date, t.status, c.name AS course,
                       (SELECT count(*) FROM timing_segments WHERE timing_event_id = t.id) AS segments,
                       (SELECT count(*) FROM timing_roster WHERE timing_event_id = t.id) AS roster
                FROM timing_events t LEFT JOIN courses c ON c.id = t.course_id
                ORDER BY t.season DESC, t.event_date DESC NULLS LAST, t.id DESC
            """)
            )
            .mappings()
            .all()
        )
        courses = s.execute(text("SELECT id, name FROM courses ORDER BY name")).mappings().all()
    return templates.TemplateResponse(
        "admin/timing.html",
        {
            "request": request,
            "events": [dict(e) for e in events],
            "courses": [dict(c) for c in courses],
            "this_year": date.today().year,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/create")
async def timing_create(
    request: Request,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    name = _form_str(form, "name")
    season_raw = _form_str(form, "season")
    date_raw = _form_str(form, "event_date")
    course_raw = _form_str(form, "course_id")
    if not name or not season_raw.isdigit():
        return RedirectResponse("/admin/timing?error=Name+and+season+are+required", status_code=303)
    try:
        event_date = date.fromisoformat(date_raw) if date_raw else None
    except ValueError:
        return RedirectResponse("/admin/timing?error=Date+must+be+YYYY-MM-DD", status_code=303)
    with get_session() as s:
        try:
            event_id = s.execute(
                text("""
                INSERT INTO timing_events (season, name, event_date, course_id, created_by)
                VALUES (:season, :name, :d, :course, :by) RETURNING id
            """),
                {
                    "season": int(season_raw),
                    "name": name,
                    "d": event_date,
                    "course": int(course_raw) if course_raw.isdigit() else None,
                    "by": user.get("id"),
                },
            ).scalar_one()
            s.commit()
        except IntegrityError as exc:
            s.rollback()
            # 23505 = unique violation (season + name); anything else is a bad course id.
            if getattr(getattr(exc, "orig", None), "sqlstate", "") == "23505":
                msg = "A+timing+event+with+that+name+already+exists+this+season"
            else:
                msg = "Unknown+course"
            return RedirectResponse(f"/admin/timing?error={msg}", status_code=303)
    return RedirectResponse(f"/admin/timing/{event_id}", status_code=303)


@router.get("/{event_id}", response_class=HTMLResponse)
def timing_event(
    request: Request,
    event_id: int,
    saved: str = "",
    error: str = "",
    user: dict = Depends(require_picl),
):
    with get_session() as s:
        event = _load_event(s, event_id)
        segments = _segments(s, event_id)
        roster = (
            s.execute(
                text("""
                SELECT id, plate, name, team, category, source
                FROM timing_roster WHERE timing_event_id = :e ORDER BY plate
            """),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
        seasons = [
            r[0]
            for r in s.execute(
                text("SELECT DISTINCT season FROM events WHERE season > 0 ORDER BY season DESC")
            ).all()
        ]
        courses = s.execute(text("SELECT id, name FROM courses ORDER BY name")).mappings().all()
    return templates.TemplateResponse(
        "admin/timing_event.html",
        {
            "request": request,
            "event": event,
            "segments": segments,
            "roster": [dict(r) for r in roster],
            "seasons": seasons,
            "courses": [dict(c) for c in courses],
            "statuses": STATUSES,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/{event_id}/segments/add")
async def segment_add(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    try:
        seg = parse_segment_form({k: v for k, v in form.items() if isinstance(v, str)})
    except ValueError as exc:
        return _redirect(event_id, error=str(exc).replace(" ", "+"))
    with get_session() as s:
        _load_event(s, event_id)
        _add_segment(s, event_id, seg)
        s.commit()
    return _redirect(event_id, saved="segment")


@router.post("/{event_id}/segments/{segment_id}")
async def segment_update(
    request: Request,
    event_id: int,
    segment_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    action = _form_str(form, "action")
    with get_session() as s:
        _load_event(s, event_id)
        if action == "delete":
            n = s.execute(
                text(
                    "DELETE FROM timing_segments WHERE id = :sid AND timing_event_id = :e "
                    "AND NOT EXISTS (SELECT 1 FROM timing_crossings c JOIN timing_points p "
                    "ON p.id = c.point_id WHERE p.segment_id = :sid)"
                ),
                {"sid": segment_id, "e": event_id},
            )
            if rowcount(n) == 0:
                return _redirect(
                    event_id, error="Segment+has+recorded+crossings+and+cannot+be+deleted"
                )
            s.commit()
            return _redirect(event_id, saved="deleted")
        try:
            seg = parse_segment_form({k: v for k, v in form.items() if isinstance(v, str)})
        except ValueError as exc:
            return _redirect(event_id, error=str(exc).replace(" ", "+"))
        s.execute(
            text("""
            UPDATE timing_segments
            SET name = :n, distance_miles = :dist, elevation_ft = :elev,
                rides_hs = :hs, rides_ms = :ms
            WHERE id = :sid AND timing_event_id = :e
        """),
            {
                "n": seg.name,
                "dist": seg.distance_miles,
                "elev": seg.elevation_ft,
                "hs": seg.rides_hs,
                "ms": seg.rides_ms,
                "sid": segment_id,
                "e": event_id,
            },
        )
        s.commit()
    return _redirect(event_id, saved="segment")


@router.post("/{event_id}/details")
async def event_update(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    name = _form_str(form, "name")
    date_raw = _form_str(form, "event_date")
    course_raw = _form_str(form, "course_id")
    status = _form_str(form, "status")
    if not name or status not in STATUSES:
        return _redirect(event_id, error="Name+and+status+are+required")
    try:
        event_date = date.fromisoformat(date_raw) if date_raw else None
    except ValueError:
        return _redirect(event_id, error="Date+must+be+YYYY-MM-DD")
    with get_session() as s:
        _load_event(s, event_id)
        s.execute(
            text("""
            UPDATE timing_events SET name = :n, event_date = :d, course_id = :c, status = :st
            WHERE id = :e
        """),
            {
                "n": name,
                "d": event_date,
                "c": int(course_raw) if course_raw.isdigit() else None,
                "st": status,
                "e": event_id,
            },
        )
        s.commit()
    return _redirect(event_id, saved="details")


# ── Roster ─────────────────────────────────────────────────────────────────


def _insert_roster(s: Session, event_id: int, rows: list[RosterRow], source: str) -> int:
    added = 0
    for r in rows:
        result = s.execute(
            text("""
            INSERT INTO timing_roster (timing_event_id, plate, name, team, category, source)
            VALUES (:e, :plate, :name, :team, :cat, :src)
            ON CONFLICT (timing_event_id, plate) DO NOTHING
        """),
            {
                "e": event_id,
                "plate": r.plate,
                "name": r.name,
                "team": r.team,
                "cat": r.category,
                "src": source,
            },
        )
        added += rowcount(result)
    return added


@router.post("/{event_id}/roster/paste")
async def roster_paste(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    try:
        rows = parse_roster_lines(_form_str(form, "lines"))
    except ValueError as exc:
        return _redirect(event_id, error=str(exc).replace(" ", "+"))
    with get_session() as s:
        _load_event(s, event_id)
        added = _insert_roster(s, event_id, rows, "paste")
        s.commit()
    return _redirect(
        event_id, saved=f"{added}+riders+added,+{len(rows) - added}+already+on+the+roster"
    )


@router.post("/{event_id}/roster/season")
async def roster_from_season(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Pre-fill the roster from a season's race results: each plate's latest name, team, category."""
    form = await request.form()
    season_raw = _form_str(form, "season")
    if not season_raw.isdigit():
        return _redirect(event_id, error="Pick+a+season")
    with get_session() as s:
        _load_event(s, event_id)
        result = s.execute(
            text("""
            INSERT INTO timing_roster (timing_event_id, plate, name, team, category, source)
            SELECT :e, x.bib, x.name, x.team, x.category, 'season'
            FROM (
                SELECT DISTINCT ON (r.bib) r.bib, ri.name, ri.team, r.category
                FROM results r
                JOIN riders ri ON ri.id = r.rider_id
                JOIN events e ON e.id = r.event_id
                WHERE e.season = :season AND e.event_type = 'points' AND e.is_published
                  AND r.bib > 0
                ORDER BY r.bib, e.event_order DESC, e.id DESC
            ) x
            ON CONFLICT (timing_event_id, plate) DO NOTHING
        """),
            {"e": event_id, "season": int(season_raw)},
        )
        added = rowcount(result)
        s.commit()
    return _redirect(event_id, saved=f"{added}+riders+added+from+{season_raw}+results")


@router.post("/{event_id}/roster/add")
async def roster_add(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    plate = _form_str(form, "plate")
    name = _form_str(form, "name")
    if not plate.isdigit() or not name:
        return _redirect(event_id, error="Plate+(a+number)+and+name+are+required")
    row = RosterRow(
        plate=int(plate),
        name=name,
        team=_form_str(form, "team") or None,
        category=_form_str(form, "category") or None,
    )
    with get_session() as s:
        _load_event(s, event_id)
        added = _insert_roster(s, event_id, [row], "manual")
        s.commit()
    if not added:
        return _redirect(event_id, error=f"Plate+{plate}+is+already+on+the+roster")
    return _redirect(event_id, saved="rider")


@router.post("/{event_id}/roster/{roster_id}/delete")
def roster_delete(
    event_id: int,
    roster_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        s.execute(
            text("DELETE FROM timing_roster WHERE id = :r AND timing_event_id = :e"),
            {"r": roster_id, "e": event_id},
        )
        s.commit()
    return _redirect(event_id, saved="removed")


@router.post("/{event_id}/roster/clear")
def roster_clear(
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        s.execute(text("DELETE FROM timing_roster WHERE timing_event_id = :e"), {"e": event_id})
        s.commit()
    return _redirect(event_id, saved="roster+cleared")


# ── Station code sheet ─────────────────────────────────────────────────────


@router.get("/{event_id}/codes", response_class=HTMLResponse)
def code_sheet(request: Request, event_id: int, _: dict = Depends(require_picl)):
    """One printable card per point: QR code, the code itself, and the URL."""
    with get_session() as s:
        event = _load_event(s, event_id)
        segments = _segments(s, event_id)
    cards = []
    for seg in segments:
        for kind in POINT_KINDS:
            point = seg["points"].get(kind)
            if not point:
                continue
            url = build_link(request, station_path(point["code"]))
            groups = [g for g, on in (("HS", seg["rides_hs"]), ("MS", seg["rides_ms"])) if on]
            cards.append(
                {
                    "segment": seg["name"],
                    "seq": seg["seq"],
                    "groups": " + ".join(groups),
                    "kind": kind,
                    "code": point["code"],
                    "url": url,
                    "qr": segno.make(url, error="m").svg_data_uri(scale=5, border=1),
                }
            )
    return templates.TemplateResponse(
        "admin/timing_codes.html", {"request": request, "event": event, "cards": cards}
    )
