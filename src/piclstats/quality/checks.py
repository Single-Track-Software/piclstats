"""Row- and event-level data-quality checks, recorded per scrape run.

Each check is a SQL predicate over one event's results. Findings go to
``dq_checks`` and roll up into ``results.dq_status``: any *error* excludes
the row from every statistic, any *warn* keeps it but flags it, otherwise
``ok``. Raw columns are never rewritten; the rollup is the only write to
``results``.

The checks encode the failure modes found in real scrapes: a wall-clock
timestamp parsed as an elapsed time (24:44:02 finishes), a place of -1, a
literal ``*`` status, splits that do not add up, bib 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Shared SQL fragments for lap arithmetic (penalty-aware split check).
from piclstats.web.queries import _ACTUAL_LAPS, _LAP_JOINS, _LAPS_CONSISTENT, _SUM_LAPS

KNOWN_STATUSES = ("OK", "DNF", "DNS", "DSQ", "DQ", "NR")

# (check, severity, predicate over alias r, observed expr, expected expr)
ROW_CHECKS: list[tuple[str, str, str, str, str]] = [
    (
        "total_time_timestamp",
        "error",
        "r.total_time >= interval '12 hours'",
        "r.total_time_raw",
        "'an elapsed time, not a clock time'",
    ),
    (
        "total_time_over_cutoff",
        "error",
        "r.total_time >= interval '2 hours' AND r.total_time < interval '12 hours'",
        "r.total_time::text",
        "'under 2:00:00'",
    ),
    (
        "place_nonpositive",
        "error",
        "r.place IS NOT NULL AND r.place <= 0",
        "r.place::text",
        "'a place of 1 or more, or none'",
    ),
    (
        "status_unknown",
        "warn",
        "r.status IS NULL OR r.status <> ALL(:known)",
        "coalesce(r.status, 'NULL')",
        "'one of OK DNF DNS DSQ DQ NR'",
    ),
    (
        "ok_without_time",
        "warn",
        "r.status = 'OK' AND r.place IS NOT NULL AND r.total_time IS NULL",
        "coalesce(nullif(r.total_time_raw, ''), '(blank)')",
        "'a finish time'",
    ),
    (
        "splits_dont_sum",
        "warn",
        f"r.status = 'OK' AND r.total_time IS NOT NULL AND r.total_time < interval '2 hours'"
        f" AND {_ACTUAL_LAPS} > 0 AND NOT {_LAPS_CONSISTENT}",
        f"({_SUM_LAPS})::text",
        "(r.total_time - COALESCE(r.penalty, '0'::interval))::text",
    ),
    ("bib_zero", "warn", "r.bib <= 0", "r.bib::text", "'a positive bib'"),
]

# Needs the course profile (dl): ridden laps differ from the division's lap
# count. Informational — a pulled rider is normal; a whole division disagreeing
# means the profile is wrong (ADR 001).
_LAPS_NE_PROFILE = f"""
    SELECT r.id, {_ACTUAL_LAPS}::text, dl.lap_count::text
    FROM results r
    JOIN events e ON e.id = r.event_id
    {_LAP_JOINS}
    WHERE r.event_id = :eid AND r.status = 'OK' AND r.place IS NOT NULL
      AND dl.lap_count IS NOT NULL AND {_ACTUAL_LAPS} > 0
      AND {_ACTUAL_LAPS} <> dl.lap_count
"""

# Event level: OK places within a category should run 1..n without gaps.
_PLACE_GAPS = """
    SELECT r.category, count(*)::text, max(r.place)::text
    FROM results r
    WHERE r.event_id = :eid AND r.place IS NOT NULL AND r.place > 0
    GROUP BY r.category
    HAVING count(*) <> max(r.place)
"""


@dataclass
class CheckSummary:
    run_id: int
    event_id: int
    findings: dict[str, int]
    excluded: int
    warned: int

    @property
    def errors(self) -> int:
        return sum(n for c, n in self.findings.items() if SEVERITY[c] == "error")


SEVERITY: dict[str, str] = {c: sev for c, sev, *_ in ROW_CHECKS}
SEVERITY.update(
    {"laps_ne_profile": "info", "place_sequence_gap": "info", "results_count_drop": "error"}
)


def rollup_status(severities: list[str]) -> str:
    """dq_status for a row given the severities of its findings."""
    if "error" in severities:
        return "excluded"
    if "warn" in severities:
        return "warn"
    return "ok"


def start_run(
    session: Session,
    *,
    raceresult_id: int,
    season: int | None,
    event_id: int | None = None,
    rows_parsed: int | None = None,
    rows_loaded: int | None = None,
    riders_new: int | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    return session.execute(
        text("""
        INSERT INTO scrape_runs
            (raceresult_id, season, event_id, rows_parsed, rows_loaded, riders_new, detail)
        VALUES (:rr, :season, :eid, :parsed, :loaded, :new, CAST(:detail AS jsonb))
        RETURNING id
        """),
        {
            "rr": raceresult_id,
            "season": season,
            "eid": event_id,
            "parsed": rows_parsed,
            "loaded": rows_loaded,
            "new": riders_new,
            "detail": json.dumps(detail or {}),
        },
    ).scalar_one()


def run_checks(session: Session, run_id: int, event_id: int) -> CheckSummary:
    """Record every finding for one event under `run_id` and roll up dq_status."""
    findings: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    def add(check: str, result_id: int | None, observed: str | None, expected: str | None) -> None:
        rows.append(
            {
                "run": run_id,
                "eid": event_id,
                "rid": result_id,
                "check": check,
                "sev": SEVERITY[check],
                "obs": observed,
                "exp": expected,
            }
        )
        findings[check] = findings.get(check, 0) + 1

    for check, _sev, predicate, observed, expected in ROW_CHECKS:
        hits = session.execute(
            text(
                f"SELECT r.id, {observed}, {expected} FROM results r WHERE r.event_id = :eid AND ({predicate})"
            ),
            {"eid": event_id, "known": list(KNOWN_STATUSES)},
        ).all()
        for rid, obs, exp in hits:
            add(check, rid, obs, exp)

    for rid, obs, exp in session.execute(text(_LAPS_NE_PROFILE), {"eid": event_id}).all():
        add("laps_ne_profile", rid, obs, exp)

    for category, placed, max_place in session.execute(text(_PLACE_GAPS), {"eid": event_id}).all():
        add(
            "place_sequence_gap",
            None,
            f"{category}: {placed} placed, max place {max_place}",
            "places 1..n",
        )

    prev = session.execute(
        text("""
        SELECT rows_loaded FROM scrape_runs
        WHERE raceresult_id = (SELECT raceresult_id FROM events WHERE id = :eid)
          AND id < :run AND rows_loaded IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """),
        {"eid": event_id, "run": run_id},
    ).scalar()
    now = session.execute(
        text("SELECT count(*) FROM results WHERE event_id = :eid"), {"eid": event_id}
    ).scalar_one()
    if prev and now < prev * 0.5:
        add("results_count_drop", None, f"{now} results", f"about {prev} as in the previous scrape")

    if rows:
        session.execute(
            text("""
            INSERT INTO dq_checks (run_id, event_id, result_id, "check", severity, observed, expected)
            VALUES (:run, :eid, :rid, :check, :sev, :obs, :exp)
            """),
            rows,
        )

    session.execute(
        text("""
        UPDATE results r SET dq_status = s.status
        FROM (
            SELECT r2.id,
                   CASE WHEN bool_or(c.severity = 'error') THEN 'excluded'
                        WHEN bool_or(c.severity = 'warn') THEN 'warn'
                        ELSE 'ok' END AS status
            FROM results r2
            LEFT JOIN dq_checks c ON c.result_id = r2.id AND c.run_id = :run
            WHERE r2.event_id = :eid
            GROUP BY r2.id
        ) s
        WHERE s.id = r.id AND r.dq_status IS DISTINCT FROM s.status
        """),
        {"run": run_id, "eid": event_id},
    )
    counts: dict[str, int] = {
        status: n
        for status, n in session.execute(
            text("SELECT dq_status, count(*) FROM results WHERE event_id = :eid GROUP BY 1"),
            {"eid": event_id},
        ).all()
    }
    session.execute(
        text("""
        UPDATE scrape_runs SET status = 'checked', finished_at = now(), event_id = :eid
        WHERE id = :run
        """),
        {"run": run_id, "eid": event_id},
    )
    session.commit()
    return CheckSummary(
        run_id=run_id,
        event_id=event_id,
        findings=findings,
        excluded=counts.get("excluded", 0),
        warned=counts.get("warn", 0),
    )


def check_event(session: Session, event_id: int, *, source: str = "backfill") -> CheckSummary:
    """Open a run for an already-loaded event and check it (used for backfills)."""
    rr, season = session.execute(
        text("SELECT raceresult_id, season FROM events WHERE id = :eid"), {"eid": event_id}
    ).one()
    run_id = start_run(
        session, raceresult_id=rr, season=season, event_id=event_id, detail={"source": source}
    )
    return run_checks(session, run_id, event_id)
