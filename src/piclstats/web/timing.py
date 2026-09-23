"""Rally timing — the timing lead's setup pages (ADR 005).

A rally is timed across segments, each with a start and a finish point. The
lead creates the event and its segments here; every point gets a station
code that volunteers join by scanning a printed QR code, so a phone needs no
account. The roster of plates is pasted in, pulled from this season's race
results (plates are stable across a season), or added one at a time.

Station pages, sync, reconciliation and publishing follow in later changes.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import segno
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from piclstats.db.engine import get_session, rowcount
from piclstats.web.auth import build_link, require_picl, require_same_origin
from piclstats.web.timing_station import DRIFT_LIMIT_MS, apply_sync, load_station
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
        SELECT sg.id, sg.seq, sg.name, sg.distance_miles, sg.elevation_ft, sg.elevation_loss_ft,
               sg.rides_hs, sg.rides_ms, p.id AS point_id, p.kind, p.station_code
        FROM timing_segments sg
        LEFT JOIN timing_points p ON p.segment_id = sg.id
        WHERE sg.timing_event_id = :eid
        ORDER BY sg.seq, p.kind DESC
    """),
        {"eid": event_id},
    ).all()
    segments: dict[int, dict[str, Any]] = {}
    for sid, seq, name, dist, elev, loss, hs, ms, point_id, kind, code in rows:
        seg = segments.setdefault(
            sid,
            {
                "id": sid,
                "seq": seq,
                "name": name,
                "distance_miles": dist,
                "elevation_ft": elev,
                "elevation_loss_ft": loss,
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
    elevation_ft: float | None  # gain
    elevation_loss_ft: float | None  # descent
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
        elevation_loss_ft=opt_float("elevation_loss_ft"),
        rides_hs=rides_hs,
        rides_ms=rides_ms,
    )


def _devices(s: Session, event_id: int) -> list[dict[str, Any]]:
    """Every phone that has synced, with its clock offset and what it has sent."""
    rows = s.execute(
        text("""
        SELECT d.id, d.label, d.joined_at, d.last_seen_at, d.offset_ms, d.offset_drift_ms,
               d.pending, sg.seq, sg.name AS segment, p.kind,
               (SELECT count(*) FROM timing_crossings c WHERE c.device_id = d.id) AS received
        FROM timing_devices d
        JOIN timing_points p ON p.id = d.point_id
        JOIN timing_segments sg ON sg.id = p.segment_id
        WHERE d.timing_event_id = :e
        ORDER BY sg.seq, p.kind DESC, d.joined_at
    """),
        {"e": event_id},
    ).mappings()
    return [dict(r) for r in rows]


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
            elevation_loss_ft, rides_hs, rides_ms)
        VALUES (:e, :seq, :name, :dist, :elev, :loss, :hs, :ms) RETURNING id
    """),
        {
            "e": event_id,
            "seq": _next_seq(s, event_id),
            "name": seg.name,
            "dist": seg.distance_miles,
            "elev": seg.elevation_ft,
            "loss": seg.elevation_loss_ft,
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
        devices = _devices(s, event_id)
    return templates.TemplateResponse(
        "admin/timing_event.html",
        {
            "request": request,
            "event": event,
            "segments": segments,
            "roster": [dict(r) for r in roster],
            "seasons": seasons,
            "courses": [dict(c) for c in courses],
            "devices": devices,
            "drift_limit_ms": DRIFT_LIMIT_MS,
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
                elevation_loss_ft = :loss, rides_hs = :hs, rides_ms = :ms
            WHERE id = :sid AND timing_event_id = :e
        """),
            {
                "n": seg.name,
                "dist": seg.distance_miles,
                "elev": seg.elevation_ft,
                "loss": seg.elevation_loss_ft,
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


@router.post("/{event_id}/delete")
def event_delete(
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Remove a rally and everything recorded for it. Refused once results are published."""
    with get_session() as s:
        event = _load_event(s, event_id)
        if event["status"] == "published":
            return _redirect(event_id, error="A+published+rally+cannot+be+deleted")
        s.execute(text("DELETE FROM timing_events WHERE id = :e"), {"e": event_id})
        s.commit()
    return RedirectResponse("/admin/timing?saved=rally+deleted", status_code=303)


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


# ── Import a station's export file (no-internet transfer) ──────────────────


@router.post("/{event_id}/import")
async def station_import(
    request: Request,
    event_id: int,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Load the JSON file a station exported, through the same path as a sync."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return _redirect(event_id, error="Choose+a+station+export+file")
    try:
        payload = json.loads((await upload.read()).decode("utf-8"))
        code = str(payload["code"]).upper()
    except Exception:
        return _redirect(event_id, error="That+is+not+a+station+export+file")
    with get_session() as s:
        _load_event(s, event_id)
        station = load_station(s, code)
        if station is None or station["event_id"] != event_id:
            return _redirect(event_id, error=f"Station+{code}+is+not+part+of+this+rally")
        ack = apply_sync(s, station, payload, author=f"import:{user.get('email', '?')}")
    return _redirect(
        event_id,
        saved=f"{ack['inserted']}+new+crossings+from+station+{code}+({len(ack['rejected'])}+rejected)",
    )


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


# ── Results: reconciliation, corrections, approval, publishing ─────────────
#
# The lead's side of the timing day. Everything below reads the append-only
# crossings, folds corrections, and hands the pure module (timing_results)
# the job of pairing, flagging and ranking. Publishing writes an ordinary
# events row (no raceresult id) with one results row per rider, segment
# times in the lap columns, so the public site needs no rally-specific views.

from datetime import datetime, time as dtime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from piclstats.db.seed import classify_event_types  # noqa: E402, F811
from piclstats.quality.keys import name_key, team_key  # noqa: E402
from piclstats.web import timing_results as tr  # noqa: E402

# Volunteers read wall-clock times off their phones and backup sheets in
# league time; the server and the database keep UTC.
LEAGUE_TZ = ZoneInfo("America/New_York")


def _rec_inputs(s: Session, event_id: int) -> dict[str, Any]:
    """Everything reconcile() needs, straight from the tables."""
    seg_rows = _segments(s, event_id)
    segments = [
        tr.Segment(
            id=sg["id"],
            seq=sg["seq"],
            name=sg["name"],
            start_point_id=sg["points"]["start"]["id"],
            finish_point_id=sg["points"]["finish"]["id"],
            rides_hs=sg["rides_hs"],
            rides_ms=sg["rides_ms"],
            distance_miles=sg["distance_miles"],
        )
        for sg in seg_rows
        if "start" in sg["points"] and "finish" in sg["points"]
    ]
    crossing_rows = [
        dict(r)
        for r in s.execute(
            text("""
            SELECT id, point_id, ts, plate, kind, supersedes, voided, note, received_at, device_id,
                   author
            FROM timing_crossings WHERE timing_event_id = :e
        """),
            {"e": event_id},
        ).mappings()
    ]
    roster = [
        tr.RosterRider(r[0], r[1], r[2], r[3])
        for r in s.execute(
            text(
                "SELECT plate, name, team, category FROM timing_roster WHERE timing_event_id = :e"
            ),
            {"e": event_id},
        ).all()
    ]
    adjustments = [
        dict(r)
        for r in s.execute(
            text("""
            SELECT id, plate, segment_id, seconds, reason, author, created_at
            FROM timing_adjustments WHERE timing_event_id = :e ORDER BY created_at
        """),
            {"e": event_id},
        ).mappings()
    ]
    accepted = {
        r[0]: {"note": r[1], "author": r[2], "at": r[3]}
        for r in s.execute(
            text(
                "SELECT flag_key, note, author, created_at FROM timing_flag_overrides "
                "WHERE timing_event_id = :e"
            ),
            {"e": event_id},
        ).all()
    }
    devices = [
        tr.DeviceStatus(
            d["id"],
            d["label"],
            d["point_id"],
            d["pending"],
            d["offset_drift_ms"],
            d["last_seen_at"],
        )
        for d in s.execute(
            text(
                "SELECT id, label, point_id, pending, offset_drift_ms, last_seen_at "
                "FROM timing_devices WHERE timing_event_id = :e"
            ),
            {"e": event_id},
        ).mappings()
    ]
    rec = tr.reconcile(
        segments,
        tr.effective_crossings(crossing_rows),
        roster,
        adjustments=[
            tr.Adjustment(a["plate"], a["seconds"], a["reason"], a["segment_id"])
            for a in adjustments
        ],
        accepted_keys=set(accepted),
        devices=devices,
        drift_limit_ms=DRIFT_LIMIT_MS,
        tz=LEAGUE_TZ,
    )
    return {
        "segments": segments,
        "seg_rows": seg_rows,
        "crossing_rows": crossing_rows,
        "rec": rec,
        "adjustments": adjustments,
        "accepted": accepted,
    }


@router.get("/{event_id}/results", response_class=HTMLResponse)
def results_page(
    request: Request,
    event_id: int,
    saved: str = "",
    error: str = "",
    _: dict = Depends(require_picl),
):
    with get_session() as s:
        event = _load_event(s, event_id)
        inp = _rec_inputs(s, event_id)
        published = None
        if event["published_event_id"]:
            published = (
                s.execute(
                    text(
                        "SELECT id, event_name, is_published, event_order FROM events WHERE id = :id"
                    ),
                    {"id": event["published_event_id"]},
                )
                .mappings()
                .first()
            )
    rec: tr.Reconciliation = inp["rec"]
    by_cat: dict[str, list[tr.RiderResult]] = {}
    for r in rec.riders:
        by_cat.setdefault(r.category or "No category", []).append(r)
    flags_block = [f for f in rec.flags if f.severity == "block" and not f.accepted]
    flags_accepted = [f for f in rec.flags if f.accepted]
    flags_info = [f for f in rec.flags if f.severity == "info" and not f.accepted]
    return templates.TemplateResponse(
        "admin/timing_results.html",
        {
            "request": request,
            "event": event,
            "segments": inp["segments"],
            "rec": rec,
            "by_cat": by_cat,
            "flags_block": flags_block,
            "flags_info": flags_info,
            "flags_accepted": flags_accepted,
            "accepted": inp["accepted"],
            "adjustments": inp["adjustments"],
            "published": dict(published) if published else None,
            "fmt": tr.format_seconds,
            "saved": saved,
            "error": error,
        },
    )


def _results_redirect(event_id: int, **params: str) -> RedirectResponse:
    query = "".join(f"&{k}={v}" for k, v in params.items())
    return RedirectResponse(f"/admin/timing/{event_id}/results?_=1{query}", status_code=303)


@router.post("/{event_id}/flags/accept")
async def flag_accept(
    request: Request,
    event_id: int,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    key = _form_str(form, "flag_key")
    note = _form_str(form, "note")
    if not key or not note:
        return _results_redirect(event_id, error="A+note+is+required+to+accept+a+flag")
    with get_session() as s:
        _load_event(s, event_id)
        s.execute(
            text("""
            INSERT INTO timing_flag_overrides (timing_event_id, flag_key, note, author)
            VALUES (:e, :k, :n, :a)
            ON CONFLICT (timing_event_id, flag_key) DO UPDATE SET note = :n, author = :a
        """),
            {"e": event_id, "k": key[:200], "n": note[:500], "a": user.get("email", "?")},
        )
        s.commit()
    return _results_redirect(event_id, saved="flag+accepted")


@router.post("/{event_id}/flags/unaccept")
async def flag_unaccept(
    request: Request,
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    with get_session() as s:
        s.execute(
            text("DELETE FROM timing_flag_overrides WHERE timing_event_id = :e AND flag_key = :k"),
            {"e": event_id, "k": _form_str(form, "flag_key")},
        )
        s.commit()
    return _results_redirect(event_id, saved="flag+reopened")


@router.post("/{event_id}/adjustments/add")
async def adjustment_add(
    request: Request,
    event_id: int,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    form = await request.form()
    plate, seconds, reason = (
        _form_str(form, "plate"),
        _form_str(form, "seconds"),
        _form_str(form, "reason"),
    )
    seg = _form_str(form, "segment_id")
    try:
        secs = float(seconds)
    except ValueError:
        return _results_redirect(event_id, error="Seconds+must+be+a+number")
    if not plate.isdigit() or not reason:
        return _results_redirect(event_id, error="Plate+and+reason+are+required")
    with get_session() as s:
        _load_event(s, event_id)
        s.execute(
            text("""
            INSERT INTO timing_adjustments (timing_event_id, plate, segment_id, seconds, reason, author)
            VALUES (:e, :p, :sg, :secs, :r, :a)
        """),
            {
                "e": event_id,
                "p": int(plate),
                "sg": int(seg) if seg.isdigit() else None,
                "secs": secs,
                "r": reason[:300],
                "a": user.get("email", "?"),
            },
        )
        s.commit()
    return _results_redirect(event_id, saved="penalty+added")


@router.post("/{event_id}/adjustments/{adj_id}/delete")
def adjustment_delete(
    event_id: int,
    adj_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        s.execute(
            text("DELETE FROM timing_adjustments WHERE id = :id AND timing_event_id = :e"),
            {"id": adj_id, "e": event_id},
        )
        s.commit()
    return _results_redirect(event_id, saved="penalty+removed")


# ── Crossings: the lead's view and corrections ─────────────────────────────


@router.get("/{event_id}/crossings", response_class=HTMLResponse)
def crossings_page(
    request: Request,
    event_id: int,
    point_id: int | None = None,
    plate: int | None = None,
    unassigned: int = 0,
    saved: str = "",
    error: str = "",
    _: dict = Depends(require_picl),
):
    with get_session() as s:
        event = _load_event(s, event_id)
        inp = _rec_inputs(s, event_id)
    points: dict[int, dict[str, Any]] = {}
    for sg in inp["seg_rows"]:
        for kind, p in sg["points"].items():
            points[p["id"]] = {"seq": sg["seq"], "segment": sg["name"], "kind": kind}
    roster = {r.plate: r for r in inp["rec"].riders}
    rows = tr.effective_crossings(inp["crossing_rows"])
    if point_id:
        rows = [c for c in rows if c.point_id == point_id]
    if plate:
        rows = [c for c in rows if c.plate == plate]
    if unassigned:
        rows = [c for c in rows if c.plate is None and not c.voided]
    history: dict[str, list[dict]] = {}
    for r in inp["crossing_rows"]:
        if r["kind"] == "correction":
            history.setdefault(r["supersedes"], []).append(r)
    return templates.TemplateResponse(
        "admin/timing_crossings.html",
        {
            "request": request,
            "event": event,
            "points": points,
            "seg_rows": inp["seg_rows"],
            "rows": rows,
            "roster": roster,
            "history": history,
            "point_id": point_id,
            "plate": plate,
            "unassigned": unassigned,
            "tz": LEAGUE_TZ,
            "saved": saved,
            "error": error,
        },
    )


def _crossings_redirect(event_id: int, form: FormData, **params: str) -> RedirectResponse:
    keep = {
        k: _form_str(form, k)
        for k in ("point_id", "plate_filter", "unassigned")
        if _form_str(form, k)
    }
    if "plate_filter" in keep:
        keep["plate"] = keep.pop("plate_filter")
    query = "".join(f"&{k}={v}" for k, v in {**keep, **params}.items())
    return RedirectResponse(f"/admin/timing/{event_id}/crossings?_=1{query}", status_code=303)


@router.post("/{event_id}/crossings/{crossing_id}/correct")
async def crossing_correct(
    request: Request,
    event_id: int,
    crossing_id: str,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Record a correction (new plate, note, void or restore) that supersedes the original."""
    form = await request.form()
    action = _form_str(form, "action")
    plate_raw = _form_str(form, "plate")
    note = _form_str(form, "note") or None
    if plate_raw and not plate_raw.isdigit():
        return _crossings_redirect(event_id, form, error="Plate+must+be+a+number")
    with get_session() as s:
        _load_event(s, event_id)
        orig = (
            s.execute(
                text(
                    "SELECT id, point_id, device_ts, offset_ms, ts, plate, voided FROM timing_crossings "
                    "WHERE id = :id AND timing_event_id = :e AND kind <> 'correction'"
                ),
                {"id": crossing_id, "e": event_id},
            )
            .mappings()
            .first()
        )
        if orig is None:
            raise HTTPException(404, "Crossing not found")
        rows = [
            dict(r)
            for r in s.execute(
                text(
                    "SELECT id, point_id, ts, plate, kind, supersedes, voided, note, received_at, device_id FROM timing_crossings WHERE timing_event_id = :e AND (id = :id OR supersedes = :id)"
                ),
                {"e": event_id, "id": crossing_id},
            ).mappings()
        ]
        current = next(c for c in tr.effective_crossings(rows) if c.id == crossing_id)
        voided = current.voided
        if action == "void":
            voided = True
        elif action == "restore":
            voided = False
        plate = int(plate_raw) if plate_raw else None
        s.execute(
            text("""
            INSERT INTO timing_crossings (id, timing_event_id, point_id, device_id, device_ts, offset_ms,
                ts, plate, kind, supersedes, voided, note, author)
            VALUES (:id, :e, :p, NULL, :device_ts, :off, :ts, :plate, 'correction', :sup, :voided,
                :note, :author)
        """),
            {
                "id": f"lead-{secrets.token_hex(8)}",
                "e": event_id,
                "p": orig["point_id"],
                "device_ts": orig["device_ts"],
                "off": orig["offset_ms"],
                "ts": orig["ts"],
                "plate": plate,
                "sup": crossing_id,
                "voided": voided,
                "note": note
                or (
                    "voided by lead"
                    if action == "void"
                    else "restored by lead"
                    if action == "restore"
                    else None
                ),
                "author": f"lead:{user.get('email', '?')}",
            },
        )
        s.commit()
    return _crossings_redirect(event_id, form, saved="correction+recorded")


def parse_clock_time(raw: str) -> dtime:
    """'10:32:05.4', '10:32:05' or '10:32' as a time of day. Raises ValueError."""
    parts = raw.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError("time must be HH:MM, HH:MM:SS or HH:MM:SS.t")
    h, m = int(parts[0]), int(parts[1])
    sec = float(parts[2]) if len(parts) == 3 else 0.0
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        raise ValueError("time out of range")
    whole = int(sec)
    return dtime(h, m, whole, int(round((sec - whole) * 1_000_000)))


@router.post("/{event_id}/crossings/manual")
async def crossing_manual(
    request: Request,
    event_id: int,
    user: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Enter a crossing from a station's paper backup sheet, flagged as manual."""
    form = await request.form()
    point_raw, plate_raw, when_raw = (
        _form_str(form, "manual_point_id"),
        _form_str(form, "manual_plate"),
        _form_str(form, "time"),
    )
    note = _form_str(form, "manual_note") or "from backup sheet"
    if not point_raw.isdigit() or not plate_raw.isdigit():
        return _crossings_redirect(event_id, form, error="Station+and+plate+are+required")
    try:
        clock = parse_clock_time(when_raw)
    except ValueError as exc:
        return _crossings_redirect(event_id, form, error=str(exc).replace(" ", "+"))
    with get_session() as s:
        event = _load_event(s, event_id)
        day = event["event_date"] or datetime.now(timezone.utc).date()
        # Times on the sheet are local wall-clock; the server runs in UTC, so
        # anchor to the event day in the league's zone.
        ts = datetime.combine(day, clock, tzinfo=LEAGUE_TZ)
        ok = s.execute(
            text(
                "SELECT 1 FROM timing_points p JOIN timing_segments sg ON sg.id = p.segment_id "
                "WHERE p.id = :p AND sg.timing_event_id = :e"
            ),
            {"p": int(point_raw), "e": event_id},
        ).first()
        if not ok:
            return _crossings_redirect(event_id, form, error="Unknown+station")
        s.execute(
            text("""
            INSERT INTO timing_crossings (id, timing_event_id, point_id, device_id, device_ts, offset_ms,
                ts, plate, kind, supersedes, voided, note, author)
            VALUES (:id, :e, :p, NULL, :ts, 0, :ts, :plate, 'manual', NULL, false, :note, :author)
        """),
            {
                "id": f"manual-{secrets.token_hex(8)}",
                "e": event_id,
                "p": int(point_raw),
                "ts": ts,
                "plate": int(plate_raw),
                "note": note[:300],
                "author": f"lead:{user.get('email', '?')}",
            },
        )
        s.commit()
    return _crossings_redirect(event_id, form, saved="manual+crossing+added")


# ── Approval and publishing ────────────────────────────────────────────────


@router.post("/{event_id}/approve")
def event_approve(
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        _load_event(s, event_id)
        rec = _rec_inputs(s, event_id)["rec"]
        if rec.blocking:
            return _results_redirect(
                event_id, error=f"{len(rec.blocking)}+flags+still+need+a+look+or+an+accept+note"
            )
        s.execute(
            text("UPDATE timing_events SET status = 'approved' WHERE id = :e"), {"e": event_id}
        )
        s.commit()
    return _results_redirect(event_id, saved="approved")


@router.post("/{event_id}/reopen")
def event_reopen(
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    """Back to live: stations may record again and the public event, if any, is hidden until republished."""
    with get_session() as s:
        event = _load_event(s, event_id)
        if event["published_event_id"]:
            s.execute(
                text("UPDATE events SET is_published = false WHERE id = :id"),
                {"id": event["published_event_id"]},
            )
        s.execute(text("UPDATE timing_events SET status = 'live' WHERE id = :e"), {"e": event_id})
        s.commit()
    return _results_redirect(
        event_id, saved="reopened;+the+public+results+are+hidden+until+you+publish+again"
    )


def publish_results(
    s: Session, event: dict[str, Any], segments: list[tr.Segment], rec: tr.Reconciliation
) -> int:
    """Write (or rewrite) the public events row and its results. Returns the events id."""
    event_id = event["published_event_id"]
    if event_id is None:
        order = s.execute(
            text("SELECT COALESCE(max(event_order), 0) + 1 FROM events WHERE season = :season"),
            {"season": event["season"]},
        ).scalar_one()
        event_id = s.execute(
            text("""
            INSERT INTO events (raceresult_id, season, event_name, event_order, event_type,
                is_published, event_date, course_id)
            VALUES (NULL, :season, :name, :ord, 'rally', true, :d, :c) RETURNING id
        """),
            {
                "season": event["season"],
                "name": event["name"],
                "ord": order,
                "d": event["event_date"],
                "c": event["course_id"],
            },
        ).scalar_one()
        s.execute(
            text("UPDATE timing_events SET published_event_id = :pid WHERE id = :e"),
            {"pid": event_id, "e": event["id"]},
        )
    else:
        s.execute(
            text("""
            UPDATE events SET event_name = :name, event_date = :d, course_id = :c, is_published = true,
                event_type = 'rally', season = :season
            WHERE id = :id
        """),
            {
                "id": event_id,
                "name": event["name"],
                "d": event["event_date"],
                "c": event["course_id"],
                "season": event["season"],
            },
        )
    if event["course_id"]:
        s.execute(
            text("""
            INSERT INTO course_race_types (course_id, season, race_type) VALUES (:c, :season, 'rally')
            ON CONFLICT (course_id, season) DO NOTHING
        """),
            {"c": event["course_id"], "season": event["season"]},
        )

    segs = sorted(segments, key=lambda x: x.seq)
    kept_bibs: list[int] = []
    for r in rec.riders:
        if r.status == "DNS":
            continue
        rider_id = s.execute(
            text("""
            INSERT INTO riders (name, team, name_key, team_key) VALUES (:name, :team, :nk, :tk)
            ON CONFLICT ON CONSTRAINT uq_riders_name_team DO UPDATE SET name = :name
            RETURNING id
        """),
            {"name": r.name, "team": r.team, "nk": name_key(r.name), "tk": team_key(r.team)},
        ).scalar_one()
        ridden = [sg for sg in segs if sg.id in r.segments]
        laps: dict[str, timedelta | None] = {f"lap{i}": None for i in range(1, 7)}
        for i, sg in enumerate(ridden[:6], start=1):
            secs = r.segments[sg.id].seconds
            laps[f"lap{i}"] = timedelta(seconds=secs) if secs is not None else None
        total = timedelta(seconds=r.total_seconds) if r.total_seconds is not None else None
        raw = {
            "source": "picl-rally-timing",
            "timing_event_id": event["id"],
            "segments": {str(sg.seq): r.segments[sg.id].seconds for sg in ridden},
            "segment_ranks": {str(sg.seq): r.segments[sg.id].rank for sg in ridden},
            "penalty_seconds": r.penalty_seconds,
            "adjustments": [
                {"seconds": a.seconds, "reason": a.reason, "segment_id": a.segment_id}
                for a in r.adjustments
            ],
            "group": r.group,
        }
        s.execute(
            text("""
            INSERT INTO results (event_id, rider_id, bib, category, gender, division, place, status,
                points, lap1, lap2, lap3, lap4, lap5, lap6, penalty, total_time, total_time_raw,
                raw_data, dq_status)
            VALUES (:eid, :rid, :bib, :cat, :gender, :division, :place, :status, NULL,
                :lap1, :lap2, :lap3, :lap4, :lap5, :lap6, :penalty, :total, :raw_total, CAST(:raw AS jsonb), 'ok')
            ON CONFLICT ON CONSTRAINT uq_results_event_bib DO UPDATE SET
                rider_id = :rid, category = :cat, gender = :gender, division = :division,
                place = :place, status = :status, points = NULL,
                lap1 = :lap1, lap2 = :lap2, lap3 = :lap3, lap4 = :lap4, lap5 = :lap5, lap6 = :lap6,
                penalty = :penalty, total_time = :total, total_time_raw = :raw_total,
                raw_data = CAST(:raw AS jsonb), dq_status = 'ok'
        """),
            {
                "eid": event_id,
                "rid": rider_id,
                "bib": r.plate,
                "cat": r.category or "Unknown",
                "gender": r.gender,
                "division": r.division,
                "place": r.place,
                "status": r.status,
                **laps,
                "penalty": timedelta(seconds=r.penalty_seconds) if r.penalty_seconds else None,
                "total": total,
                "raw_total": tr.format_seconds(r.total_seconds),
                "raw": json.dumps(raw),
            },
        )
        kept_bibs.append(r.plate)
    if kept_bibs:
        s.execute(
            text("DELETE FROM results WHERE event_id = :eid AND NOT (bib = ANY(:bibs))"),
            {"eid": event_id, "bibs": kept_bibs},
        )
    else:
        s.execute(text("DELETE FROM results WHERE event_id = :eid"), {"eid": event_id})
    s.execute(
        text("UPDATE timing_events SET status = 'published' WHERE id = :e"), {"e": event["id"]}
    )
    classify_event_types(s)
    return event_id


@router.post("/{event_id}/publish")
def event_publish(
    event_id: int,
    _: dict = Depends(require_picl),
    __: None = Depends(require_same_origin),
):
    with get_session() as s:
        event = _load_event(s, event_id)
        if event["status"] not in ("approved", "published"):
            return _results_redirect(event_id, error="Approve+the+results+first")
        inp = _rec_inputs(s, event_id)
        if inp["rec"].blocking:
            return _results_redirect(
                event_id, error="Flags+changed+since+approval;+look+at+them+first"
            )
        published_id = publish_results(s, event, inp["segments"], inp["rec"])
        s.commit()
    return _results_redirect(event_id, saved=f"published+as+event+{published_id}")


@router.get("/{event_id}/results.csv")
def results_csv(event_id: int, _: dict = Depends(require_picl)):
    import csv
    import io

    with get_session() as s:
        event = _load_event(s, event_id)
        inp = _rec_inputs(s, event_id)
    buf = io.StringIO()
    csv.writer(buf).writerows(tr.csv_rows(inp["rec"], inp["segments"]))
    name = re.sub(r"[^A-Za-z0-9]+", "-", f"{event['season']}-{event['name']}").strip("-").lower()
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}-results.csv"'},
    )
