"""Data assembly for the admin DQ page (ADR 002 §4.4)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.quality.diagrams import merge_map_svg, pipeline_flow_svg, sparkline_svg

# (metric, label, good direction) shown as cards with delta + sparkline.
CARD_METRICS: list[tuple[str, str, str]] = [
    ("results_excluded_pct", "Excluded rows %", "down"),
    ("results_warn_pct", "Flagged rows %", "down"),
    ("timestamp_totals", "Clock-time totals", "down"),
    ("place_nonpositive", "Non-positive places", "down"),
    ("events_checked_pct", "Events checked %", "up"),
    ("events_mapped_to_course_pct", "Events on a course %", "up"),
    ("rider_alias_groups", "Rider alias groups", "flat"),
    ("name_key_groups_unmerged", "Unmerged name groups", "down"),
    ("conflict_name_groups", "Same-name conflicts", "flat"),
    ("team_variant_groups", "Team spelling groups", "down"),
    ("golden_overall_pass_pct", "Golden fixtures pass %", "up"),
    ("golden_negative_pairs_pass_pct", "Never-merge pairs kept %", "up"),
]

CHECK_HELP = {
    "try_it_out": "A Single Lap try-it-out category, not a race. Shown in results, excluded from every statistic.",
    "total_time_timestamp": "Finish time is a clock time (e.g. 24:44:02), not an elapsed time. Excluded.",
    "total_time_over_cutoff": "Finish over 2 hours; no PICL race runs that long. Excluded.",
    "place_nonpositive": "Place of 0 or below in the source. Excluded.",
    "status_unknown": "Status is not OK/DNF/DNS/DSQ/DQ/NR (the source prints * for unranked).",
    "ok_without_time": "Placed as OK but no finish time was published.",
    "splits_dont_sum": "Lap splits do not add up to the ride time (total minus penalty).",
    "bib_zero": "Bib missing or 0; two such rows in one event overwrite each other.",
    "laps_ne_profile": "Rider recorded a different lap count than the division profile for the course.",
    "place_sequence_gap": "Places within a category do not run 1..n.",
    "results_count_drop": "This scrape returned under half the rows of the previous one.",
}


def _one(session: Session, sql: str, **p: Any) -> Any:
    return session.execute(text(sql), p).scalar()


def flow(session: Session) -> dict[str, Any]:
    s: dict[str, Any] = {}
    s["events"] = _one(session, "SELECT count(*) FROM events") or 0
    s["results"] = _one(session, "SELECT count(*) FROM results") or 0
    by: dict[str, int] = {
        k: v
        for k, v in session.execute(
            text("SELECT dq_status, count(*) FROM results GROUP BY 1")
        ).all()
    }
    s["excluded"] = by.get("excluded", 0)
    s["warn"] = by.get("warn", 0)
    s["checked"] = (
        _one(session, "SELECT count(DISTINCT event_id) FROM scrape_runs WHERE status <> 'loaded'")
        or 0
    )
    riders = _one(session, "SELECT count(*) FROM riders") or 0
    keyed = _one(session, "SELECT count(*) FROM riders WHERE name_key IS NOT NULL") or 0
    s["riders"], s["keyed_pct"] = riders, (100.0 * keyed / riders if riders else 0.0)
    s["alias_groups"] = _one(session, "SELECT count(DISTINCT canonical_id) FROM rider_aliases") or 0
    s["aliases"] = _one(session, "SELECT count(*) FROM rider_aliases") or 0
    s["gate"] = _one(
        session,
        "SELECT value FROM picl_dq_metrics WHERE metric = 'gate_passed' ORDER BY captured_at DESC LIMIT 1",
    )
    s["published"] = (
        _one(session, "SELECT count(DISTINCT event_id) FROM scrape_runs WHERE status = 'published'")
        or 0
    )
    if s["gate"] is None:
        gate = ("GATE", "—", "no runs yet", "#6b7280")
    elif s["gate"] >= 1:
        gate = ("GATE", "PASS", "published", "#16a34a")
    else:
        gate = ("GATE", "BLOCK", "held", "#dc2626")
    nodes = [
        ("SCRAPE", f"{s['events']}", "events", "#3b82f6"),
        ("results", f"{s['results']:,}", f"{s['excluded']} excluded", "#111827"),
        ("CHECK", f"{s['checked']}/{s['events']}", f"{s['warn']} flagged", "#8b5cf6"),
        ("CANONICALIZE", f"{s['keyed_pct']:.0f}%", "riders keyed", "#8b5cf6"),
        ("MERGE", f"{s['alias_groups']}", f"{s['aliases']} aliases", "#14b8a6"),
        gate,
        ("PUBLISHED", f"{s['published']}", "events", "#16a34a"),
    ]
    s["svg"] = pipeline_flow_svg(nodes)
    return s


def cards(session: Session, runs: int = 12) -> list[dict[str, Any]]:
    run_ids = [
        r[0]
        for r in session.execute(
            text(
                "SELECT run_id FROM picl_dq_metrics GROUP BY run_id ORDER BY min(captured_at) LIMIT :n"
            ),
            {"n": runs},
        ).all()
    ]
    # newest N: fetch all then keep the tail
    run_ids = [
        r[0]
        for r in session.execute(
            text(
                "SELECT run_id FROM picl_dq_metrics GROUP BY run_id ORDER BY min(captured_at) DESC LIMIT :n"
            ),
            {"n": runs},
        ).all()
    ][::-1]
    if not run_ids:
        return []
    series: dict[str, dict[int, float | None]] = {}
    for run_id, metric, value in session.execute(
        text("SELECT run_id, metric, value FROM picl_dq_metrics WHERE run_id = ANY(:ids)"),
        {"ids": run_ids},
    ).all():
        series.setdefault(metric, {})[run_id] = value
    out = []
    for metric, label, good in CARD_METRICS:
        vals = [series.get(metric, {}).get(r) for r in run_ids]
        now = vals[-1]
        prev = next((v for v in reversed(vals[:-1]) if v is not None), None)
        delta = None if now is None or prev is None else round(now - prev, 2)
        tone = "text-gray-500"
        if delta:
            improving = (delta < 0) if good == "down" else (delta > 0) if good == "up" else None
            tone = (
                "text-green-600"
                if improving
                else ("text-red-600" if improving is False else "text-gray-500")
            )
        out.append(
            {
                "metric": metric,
                "label": label,
                "value": "—"
                if now is None
                else (
                    f"{now:.1f}" if isinstance(now, float) and now != int(now) else f"{int(now):,}"
                ),
                "delta": delta,
                "tone": tone,
                "spark": sparkline_svg(vals),
            }
        )
    return out


def recent_runs(session: Session, n: int = 15) -> list[dict[str, Any]]:
    return [
        dict(r._mapping)
        for r in session.execute(
            text("""
            SELECT s.id, s.raceresult_id, s.season, e.event_name, s.status, s.rows_loaded,
                   s.riders_new, s.gate_passed, s.gate_reasons, s.started_at,
                   (SELECT count(*) FROM dq_checks c WHERE c.run_id = s.id AND c.severity = 'error') AS errors,
                   (SELECT count(*) FROM dq_checks c WHERE c.run_id = s.id AND c.severity = 'warn') AS warns
            FROM scrape_runs s LEFT JOIN events e ON e.id = s.event_id
            ORDER BY s.id DESC LIMIT :n
            """),
            {"n": n},
        ).all()
    ]


def findings(session: Session, check: str | None = None) -> dict[str, Any]:
    """Counts per check from the latest run of each event; rows for one check."""
    latest = """
        latest AS (SELECT event_id, max(run_id) AS run_id FROM dq_checks GROUP BY event_id)
    """
    counts = [
        {"check": c, "severity": sev, "n": n, "events": ev, "help": CHECK_HELP.get(c, "")}
        for c, sev, n, ev in session.execute(
            text(f"""
            WITH {latest}
            SELECT c."check", c.severity, count(*), count(DISTINCT c.event_id)
            FROM dq_checks c JOIN latest l ON l.event_id = c.event_id AND l.run_id = c.run_id
            GROUP BY c."check", c.severity
            ORDER BY CASE c.severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, count(*) DESC
            """)
        ).all()
    ]
    rows: list[dict[str, Any]] = []
    if check:
        rows = [
            dict(r._mapping)
            for r in session.execute(
                text(f"""
                WITH {latest}
                SELECT e.season, e.event_name, e.id AS event_id, r.bib, ri.name AS rider, r.category,
                       c.observed, c.expected, c.severity
                FROM dq_checks c
                JOIN latest l ON l.event_id = c.event_id AND l.run_id = c.run_id
                JOIN events e ON e.id = c.event_id
                LEFT JOIN results r ON r.id = c.result_id
                LEFT JOIN riders ri ON ri.id = r.rider_id
                WHERE c."check" = :check
                ORDER BY e.season DESC, e.event_order DESC, r.category, r.bib
                LIMIT 300
                """),
                {"check": check},
            ).all()
        ]
    return {"counts": counts, "rows": rows, "check": check, "help": CHECK_HELP.get(check or "", "")}


def lineage(session: Session, q: str = "") -> dict[str, Any] | None:
    """Pick one canonical (best match for q, else the busiest rider) and its edges."""
    latest = """
        latest AS (SELECT level, max(run_id) AS run_id FROM picl_lineage_runs GROUP BY level)
    """
    q = q.strip()
    if q:
        hit = session.execute(
            text(f"""
            WITH {latest},
            named AS (
                SELECT l.level, l.canonical_key,
                       CASE WHEN l.level = 'rider' THEN (SELECT name FROM riders WHERE id = l.canonical_key::int)
                            ELSE l.canonical_key END AS display,
                       l.raw_value, l.volume
                FROM picl_lineage l JOIN latest x ON x.level = l.level AND x.run_id = l.run_id
            )
            SELECT level, canonical_key, display
            FROM named
            WHERE display ILIKE :pat OR raw_value ILIKE :pat OR canonical_key ILIKE :pat
            GROUP BY level, canonical_key, display
            ORDER BY (lower(display) = lower(:q)) DESC, sum(volume) DESC
            LIMIT 1
            """),
            {"pat": f"%{q}%", "q": q},
        ).one_or_none()
    else:
        hit = session.execute(
            text(f"""
            WITH {latest}
            SELECT l.level, l.canonical_key,
                   (SELECT name FROM riders WHERE id = l.canonical_key::int) AS display
            FROM picl_lineage l JOIN latest x ON x.level = l.level AND x.run_id = l.run_id
            WHERE l.level = 'rider'
            GROUP BY l.level, l.canonical_key
            ORDER BY count(*) DESC, sum(l.volume) DESC LIMIT 1
            """)
        ).one_or_none()
    if not hit:
        return None
    level, key, display = hit
    edges = [
        dict(r._mapping)
        for r in session.execute(
            text(f"""
            WITH {latest}
            SELECT l.raw_value, l.raw_id, l.source, l.origin, l.mechanism, l.match_score, l.volume, l.stage
            FROM picl_lineage l JOIN latest x ON x.level = l.level AND x.run_id = l.run_id
            WHERE l.level = :level AND l.canonical_key = :key
            ORDER BY l.volume DESC, l.raw_value
            """),
            {"level": level, "key": key},
        ).all()
    ]
    total = sum(int(e["volume"] or 0) for e in edges)
    canonical = display or key
    return {
        "level": level,
        "key": key,
        "canonical": canonical,
        "edges": edges,
        "total": total,
        "svg": merge_map_svg(canonical, edges, total),
        "q": q,
    }


def discovered(session: Session, n: int = 20) -> list[dict[str, Any]]:
    return [
        dict(r._mapping)
        for r in session.execute(
            text("""
            SELECT d.raceresult_id, d.season, d.name, d.status, d.note, d.found_at,
                   e.id AS event_id, e.is_published
            FROM discovered_events d LEFT JOIN events e ON e.raceresult_id = d.raceresult_id
            ORDER BY d.found_at DESC LIMIT :n
            """),
            {"n": n},
        ).all()
    ]


def unpublished(session: Session) -> list[dict[str, Any]]:
    return [
        dict(r._mapping)
        for r in session.execute(
            text("""
            SELECT e.id, e.season, e.event_name, e.raceresult_id,
                   (SELECT count(*) FROM results r WHERE r.event_id = e.id) AS rows,
                   (SELECT gate_reasons FROM scrape_runs s WHERE s.event_id = e.id ORDER BY s.id DESC LIMIT 1) AS reasons
            FROM events e WHERE NOT e.is_published
            ORDER BY e.season DESC, e.event_order DESC
            """)
        ).all()
    ]


def golden(session: Session) -> dict[str, Any]:
    from piclstats.quality.scorecard import GOLDEN_KINDS, evaluate_golden

    rows = evaluate_golden(session)
    # Names make the fixture list readable.
    rider_ids = {
        v
        for r in rows
        if r["kind"] == "rider_canonical"
        for v in (r["subject"].get("rider_id"), r["want"], r["got"])
        if v
    }
    names: dict[int, str] = {}
    if rider_ids:
        names = {
            i: f"{n} ({t or 'no team'})"
            for i, n, t in session.execute(
                text("SELECT id, name, team FROM riders WHERE id = ANY(:ids)"),
                {"ids": list(rider_ids)},
            ).all()
        }
    for r in rows:
        r["names"] = names
    pairs = [
        dict(x._mapping)
        for x in session.execute(
            text("""
            SELECT p.id, p.rider_id_a, p.rider_id_b, p.should_merge, p.note,
                   ra.name AS name_a, ra.team AS team_a, rb.name AS name_b, rb.team AS team_b,
                   COALESCE(aa.canonical_id, p.rider_id_a) = COALESCE(ab.canonical_id, p.rider_id_b) AS merged
            FROM picl_golden_pairs p
            JOIN riders ra ON ra.id = p.rider_id_a JOIN riders rb ON rb.id = p.rider_id_b
            LEFT JOIN rider_aliases aa ON aa.rider_id = p.rider_id_a
            LEFT JOIN rider_aliases ab ON ab.rider_id = p.rider_id_b
            ORDER BY p.id
            """)
        ).all()
    ]
    return {"rows": rows, "pairs": pairs, "kinds": GOLDEN_KINDS}


def page(session: Session, q: str = "", check: str | None = None) -> dict[str, Any]:
    return {
        "golden": golden(session),
        "discovered": discovered(session),
        "unpublished": unpublished(session),
        "flow": flow(session),
        "cards": cards(session),
        "runs": recent_runs(session),
        "findings": findings(session, check),
        "lineage": lineage(session, q),
    }
