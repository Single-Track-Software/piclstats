"""Rally timing — the station page volunteers run on their phones (ADR 005).

A volunteer opens `/timing/s/{code}` from the printed card. The page carries
everything it needs (event, segment, point, roster, server time) so it works
with no signal once loaded: a service worker keeps the page, and every
crossing lives in the phone's own database until it syncs.

Sync is `POST /timing/api/s/{code}/sync`: a batch of crossings with
client-generated ids, inserted append-only (a resend never duplicates), plus
the device's clock offset from server time so each crossing gets a corrected
timestamp. The lead's file import reuses the same path for a station that
never got a connection.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.db.engine import get_session, rowcount
from piclstats.web.templating import Jinja2Templates

router = APIRouter(tags=["timing-station"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CROSSING_KINDS = ("tap", "manual", "correction")
# Riders leave a start point this many seconds apart; the page shows the
# start captain how long since the last rider went.
START_INTERVAL_S = 30
# A device whose clock offset moved more than this between two syncs cannot
# be trusted to the one-second requirement (N2); the lead's view flags it.
DRIFT_LIMIT_MS = 1000
MAX_BATCH = 2000

_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# ── Pure helpers ───────────────────────────────────────────────────────────


def corrected_ts(device_ts_ms: int, offset_ms: int) -> datetime:
    """Server-clock time of a crossing: device time minus the device's offset."""
    return datetime.fromtimestamp((device_ts_ms - offset_ms) / 1000, tz=timezone.utc)


def device_offset_update(
    old_offset_ms: int | None, old_drift_ms: int, new_offset_ms: int
) -> tuple[int, int]:
    """New (offset, drift): drift is the largest offset change ever seen for the device."""
    if old_offset_ms is None:
        return new_offset_ms, old_drift_ms
    return new_offset_ms, max(old_drift_ms, abs(new_offset_ms - old_offset_ms))


def normalize_crossing(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one crossing from a sync batch. Raises ValueError with the reason."""
    cid = raw.get("id")
    if not isinstance(cid, str) or not _ID.match(cid):
        raise ValueError("bad id")
    kind = raw.get("kind", "tap")
    if kind not in CROSSING_KINDS:
        raise ValueError(f"bad kind {kind!r}")
    device_ts = raw.get("device_ts_ms")
    if (
        not isinstance(device_ts, (int, float))
        or not 1_600_000_000_000 < device_ts < 4_100_000_000_000
    ):
        raise ValueError("bad device_ts_ms")
    offset = raw.get("offset_ms", 0)
    if not isinstance(offset, (int, float)) or abs(offset) > 366 * 86_400_000:
        raise ValueError("bad offset_ms")
    plate = raw.get("plate")
    if plate is not None:
        if isinstance(plate, str) and plate.strip().isdigit():
            plate = int(plate.strip())
        if not isinstance(plate, int) or isinstance(plate, bool) or not 0 < plate < 100_000:
            raise ValueError("bad plate")
    supersedes = raw.get("supersedes")
    if supersedes is not None and (not isinstance(supersedes, str) or not _ID.match(supersedes)):
        raise ValueError("bad supersedes")
    if kind == "correction" and supersedes is None:
        raise ValueError("a correction must supersede a crossing")
    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("bad note")
    return {
        "id": cid,
        "kind": kind,
        "device_ts_ms": int(device_ts),
        "offset_ms": int(offset),
        "plate": plate,
        "supersedes": supersedes,
        "voided": bool(raw.get("voided", False)),
        "note": (note or "").strip()[:500] or None,
    }


# ── DB ─────────────────────────────────────────────────────────────────────


def load_station(s: Session, code: str) -> dict[str, Any] | None:
    row = (
        s.execute(
            text("""
            SELECT p.id AS point_id, p.kind, sg.id AS segment_id, sg.seq, sg.name AS segment,
                   sg.distance_miles, sg.rides_hs, sg.rides_ms,
                   t.id AS event_id, t.name AS event, t.season, t.event_date, t.status
            FROM timing_points p
            JOIN timing_segments sg ON sg.id = p.segment_id
            JOIN timing_events t ON t.id = sg.timing_event_id
            WHERE p.station_code = :code
        """),
            {"code": code},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def load_roster(s: Session, event_id: int) -> list[dict[str, Any]]:
    rows = s.execute(
        text("""
        SELECT plate, name, team, category FROM timing_roster
        WHERE timing_event_id = :e ORDER BY plate
    """),
        {"e": event_id},
    ).all()
    return [{"plate": r[0], "name": r[1], "team": r[2], "category": r[3]} for r in rows]


def apply_sync(
    s: Session, station: dict[str, Any], payload: dict[str, Any], *, author: str
) -> dict[str, Any]:
    """Insert a batch of crossings append-only and record the device. Returns the ack."""
    raw_list = payload.get("crossings") or []
    if not isinstance(raw_list, list) or len(raw_list) > MAX_BATCH:
        raise HTTPException(400, "crossings must be a list of at most %d" % MAX_BATCH)
    rejected: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    for raw in raw_list:
        try:
            rows.append(normalize_crossing(raw if isinstance(raw, dict) else {}))
        except ValueError as exc:
            rejected.append({"id": str((raw or {}).get("id", "?"))[:64], "reason": str(exc)})

    device: dict[str, Any] = payload["device"] if isinstance(payload.get("device"), dict) else {}
    raw_device_id = device.get("id")
    device_id = (
        raw_device_id if isinstance(raw_device_id, str) and _ID.match(raw_device_id) else None
    )
    server_now = datetime.now(timezone.utc)
    server_now_ms = int(server_now.timestamp() * 1000)
    offset_ms: int | None = None
    drift_ms = 0
    if device_id:
        client_offset = device.get("offset_ms")
        if isinstance(client_offset, (int, float)) and abs(client_offset) < 366 * 86_400_000:
            offset_ms = int(client_offset)
        else:
            client_time = payload.get("client_time_ms")
            if isinstance(client_time, (int, float)):
                offset_ms = int(client_time) - server_now_ms
        prev = s.execute(
            text("SELECT offset_ms, offset_drift_ms FROM timing_devices WHERE id = :id"),
            {"id": device_id},
        ).first()
        if offset_ms is not None:
            offset_ms, drift_ms = device_offset_update(
                prev[0] if prev else None, prev[1] if prev else 0, offset_ms
            )
        elif prev:
            offset_ms, drift_ms = prev[0], prev[1]
        s.execute(
            text("""
            INSERT INTO timing_devices (id, timing_event_id, point_id, label, user_agent,
                last_seen_at, offset_ms, offset_drift_ms, pending)
            VALUES (:id, :e, :p, :label, :ua, :now, :off, :drift, :pending)
            ON CONFLICT (id) DO UPDATE SET
                point_id = :p, label = COALESCE(:label, timing_devices.label),
                user_agent = COALESCE(:ua, timing_devices.user_agent),
                last_seen_at = :now, offset_ms = :off, offset_drift_ms = :drift,
                pending = :pending
        """),
            {
                "id": device_id,
                "e": station["event_id"],
                "p": station["point_id"],
                "label": (str(device.get("label") or "")[:80] or None),
                "ua": (str(device.get("user_agent") or "")[:300] or None),
                "now": server_now,
                "off": offset_ms,
                "drift": drift_ms,
                "pending": max(0, int(device.get("pending") or 0))
                if str(device.get("pending", 0)).lstrip("-").isdigit()
                else 0,
            },
        )

    inserted = 0
    for r in rows:
        result = s.execute(
            text("""
            INSERT INTO timing_crossings (id, timing_event_id, point_id, device_id, device_ts,
                offset_ms, ts, plate, kind, supersedes, voided, note, author)
            VALUES (:id, :e, :p, :dev, :device_ts, :off, :ts, :plate, :kind, :sup, :voided,
                :note, :author)
            ON CONFLICT (id) DO NOTHING
        """),
            {
                "id": r["id"],
                "e": station["event_id"],
                "p": station["point_id"],
                "dev": device_id,
                "device_ts": datetime.fromtimestamp(r["device_ts_ms"] / 1000, tz=timezone.utc),
                "off": r["offset_ms"],
                "ts": corrected_ts(r["device_ts_ms"], r["offset_ms"]),
                "plate": r["plate"],
                "kind": r["kind"],
                "sup": r["supersedes"],
                "voided": r["voided"],
                "note": r["note"],
                "author": author,
            },
        )
        inserted += rowcount(result)
    s.commit()
    return {
        "acked": [r["id"] for r in rows],
        "rejected": rejected,
        "inserted": inserted,
        "server_time_ms": server_now_ms,
        "offset_ms": offset_ms,
        "drift_ms": drift_ms,
        "drift_limit_ms": DRIFT_LIMIT_MS,
    }


# ── Routes ─────────────────────────────────────────────────────────────────


def _station_or_404(s: Session, code: str) -> dict[str, Any]:
    station = load_station(s, code.upper())
    if station is None:
        raise HTTPException(404, "Unknown station code")
    return station


@router.get("/timing/s/{code}", response_class=HTMLResponse)
def station_page(request: Request, code: str):
    with get_session() as s:
        station = load_station(s, code.upper())
        if station is None:
            return templates.TemplateResponse(
                "timing/station_missing.html", {"request": request, "code": code}, status_code=404
            )
        roster = load_roster(s, station["event_id"])
    context = {
        "code": code.upper(),
        "event": {
            "id": station["event_id"],
            "name": station["event"],
            "season": station["season"],
            "date": station["event_date"].isoformat() if station["event_date"] else None,
        },
        "segment": {"seq": station["seq"], "name": station["segment"]},
        "point": {"id": station["point_id"], "kind": station["kind"]},
        "roster": roster,
        "server_time_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "interval_s": START_INTERVAL_S,
        "closed": station["status"] in ("approved", "published"),
        "drift_limit_ms": DRIFT_LIMIT_MS,
    }
    response = templates.TemplateResponse(
        "timing/station.html",
        {
            "request": request,
            "station": station,
            "code": code.upper(),
            "ctx_json": json.dumps(context),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/timing/s/{code}/manifest.webmanifest")
def station_manifest(code: str):
    with get_session() as s:
        station = _station_or_404(s, code)
    manifest = {
        "name": f"PICL Timing — Seg {station['seq']} {station['kind'].title()}",
        "short_name": f"S{station['seq']} {station['kind'][:1].upper()}",
        "start_url": f"/timing/s/{code.upper()}",
        "scope": "/timing/",
        "display": "standalone",
        "background_color": "#0b1f3d",
        "theme_color": "#0b1f3d",
        "icons": [{"src": "/static/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"}],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


_SW = """
// PICL timing station: keep the station page and its assets available offline.
const CACHE = 'piclstats-timing-v1';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/timing/api/')) return;
  const keep = url.pathname.startsWith('/timing/s/') || url.pathname.startsWith('/static/');
  if (!keep) return;
  e.respondWith(
    fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request, {ignoreSearch: url.pathname.startsWith('/static/')}))
  );
});
"""


@router.get("/timing/sw.js")
def service_worker():
    return Response(
        _SW.strip() + "\n",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/timing/api/s/{code}/sync")
async def station_sync(request: Request, code: str):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Body must be a JSON object")
    with get_session() as s:
        station = _station_or_404(s, code)
        if station["status"] in ("approved", "published"):
            raise HTTPException(409, "This rally's results are closed")
        device: dict[str, Any] = (
            payload["device"] if isinstance(payload.get("device"), dict) else {}
        )
        author = f"device:{device.get('id', '?')}"[:80]
        return apply_sync(s, station, payload, author=author)


@router.get("/timing/api/s/{code}/roster")
def station_roster(code: str):
    with get_session() as s:
        station = _station_or_404(s, code)
        return {"roster": load_roster(s, station["event_id"])}
