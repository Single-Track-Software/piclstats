"""Scorecard metrics, golden fixtures, and the publish gate (ADR 002).

A scorecard run is a set of ``picl_dq_metrics`` rows sharing a ``run_id``:
one row per metric, long format, so trends are a GROUP BY. The gate verdict
is stored as one more metric (``gate_passed`` 1/0 with the reasons in
``detail``), exactly as gvpd does it. ``evaluate_gate`` is pure so the rules
are unit-tested without a database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.db.engine import rowcount

GOLDEN_FLOOR = 90.0  # positive golden pass % must be at least this to publish
GOLDEN_REGRESS_TOL = 2.0  # ...and must not drop more than this vs the previous run
EVENT_EXCLUDED_MAX_PCT = 5.0  # one event with more bad rows than this is held


@dataclass
class Metric:
    metric: str
    value: float | None
    numerator: int | None = None
    denominator: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def evaluate_gate(
    curr: dict[str, float | None], prev: dict[str, float | None] | None
) -> tuple[bool, list[str]]:
    """Decide whether the data behind `curr` may be published.

    Three kinds of rule, as in gvpd: an absolute floor, no regression against
    the previous run, and contamination counters that must not increase.
    """
    reasons: list[str] = []
    g = curr.get("golden_overall_pass_pct")
    if g is not None and g < GOLDEN_FLOOR:
        reasons.append(f"golden fixtures {g}% < floor {GOLDEN_FLOOR}%")
    npp = curr.get("golden_negative_pairs_pass_pct")
    if npp is not None and npp < 100:
        reasons.append(f"negative golden pairs merged: only {npp}% stayed distinct")
    ev = curr.get("event_excluded_pct")
    if ev is not None and ev > EVENT_EXCLUDED_MAX_PCT:
        reasons.append(f"{ev}% of this event's rows failed checks (> {EVENT_EXCLUDED_MAX_PCT}%)")
    if curr.get("event_results_count_drop"):
        reasons.append("this scrape returned under half the rows of the previous one")
    if prev:
        pg = prev.get("golden_overall_pass_pct")
        if g is not None and pg is not None and g < pg - GOLDEN_REGRESS_TOL:
            reasons.append(f"golden fixtures regressed {pg}% -> {g}%")
        for counter in ("timestamp_totals", "place_nonpositive", "conflict_name_groups"):
            c, p = curr.get(counter), prev.get(counter)
            if c is not None and p is not None and c > p:
                reasons.append(f"{counter} increased {int(p)} -> {int(c)}")
    return (not reasons, reasons)


def seed_golden_pairs_from_conflicts(session: Session) -> int:
    """Every same-name pair that raced in the same event must never merge."""
    from piclstats.db.merge import find_conflicts

    added = 0
    for group in find_conflicts(session):
        ids = sorted(e["rider_id"] for e in group["entries"])
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                res = session.execute(
                    text("""
                    INSERT INTO picl_golden_pairs (rider_id_a, rider_id_b, should_merge, note)
                    VALUES (:a, :b, false, :note)
                    ON CONFLICT (rider_id_a, rider_id_b) DO NOTHING
                    """),
                    {"a": a, "b": b, "note": f"{group['name']}: raced in the same event"},
                )
                added += rowcount(res)
    return added


def _golden_metrics(session: Session) -> list[Metric]:
    out: list[Metric] = []
    failures: list[str] = []
    passed = total = 0
    for kind, subject, expected, note in session.execute(
        text("SELECT kind, subject, expected, note FROM picl_golden")
    ).all():
        total += 1
        ok = False
        if kind == "rider_canonical":
            got = session.execute(
                text(
                    "SELECT COALESCE(canonical_id, :id) FROM (SELECT :id AS id) x "
                    "LEFT JOIN rider_aliases a ON a.rider_id = :id"
                ),
                {"id": subject["rider_id"]},
            ).scalar()
            ok = got == expected["canonical_id"]
        elif kind in ("event_type", "event_course"):
            col = "e.event_type" if kind == "event_type" else "c.name"
            got = session.execute(
                text(
                    f"SELECT {col} FROM events e LEFT JOIN courses c ON c.id = e.course_id "
                    "WHERE e.raceresult_id = :rr"
                ),
                {"rr": subject["raceresult_id"]},
            ).scalar()
            ok = got == expected.get(kind.split("_")[1])
        if ok:
            passed += 1
        else:
            failures.append(
                f"{kind} {json.dumps(subject)} expected {json.dumps(expected)} ({note or ''})"
            )
    out.append(
        Metric(
            "golden_overall_pass_pct",
            _pct(passed, total),
            passed,
            total,
            {"failures": failures[:50]},
        )
    )

    pairs = session.execute(
        text("""
        SELECT p.rider_id_a, p.rider_id_b, p.should_merge,
               COALESCE(a.canonical_id, p.rider_id_a), COALESCE(b.canonical_id, p.rider_id_b)
        FROM picl_golden_pairs p
        LEFT JOIN rider_aliases a ON a.rider_id = p.rider_id_a
        LEFT JOIN rider_aliases b ON b.rider_id = p.rider_id_b
        """)
    ).all()
    npass = sum(1 for _, _, should, ca, cb in pairs if (ca == cb) == should)
    bad = [f"{a}/{b}" for a, b, should, ca, cb in pairs if (ca == cb) != should]
    out.append(
        Metric(
            "golden_negative_pairs_pass_pct",
            _pct(npass, len(pairs)),
            npass,
            len(pairs),
            {"failures": bad[:50]},
        )
    )
    return out


def compute_metrics(session: Session, event_id: int | None = None) -> list[Metric]:
    m: list[Metric] = []
    one = lambda sql, **p: session.execute(text(sql), p).scalar_one()  # noqa: E731

    total = one("SELECT count(*) FROM results")
    excluded = one("SELECT count(*) FROM results WHERE dq_status = 'excluded'")
    warned = one("SELECT count(*) FROM results WHERE dq_status = 'warn'")
    m.append(Metric("results_total", total, total, total))
    m.append(Metric("results_excluded_pct", _pct(excluded, total), excluded, total))
    m.append(Metric("results_warn_pct", _pct(warned, total), warned, total))
    m.append(
        Metric(
            "timestamp_totals",
            one("SELECT count(*) FROM results WHERE total_time >= interval '12 hours'"),
        )
    )
    m.append(Metric("place_nonpositive", one("SELECT count(*) FROM results WHERE place <= 0")))

    events = one("SELECT count(*) FROM events")
    mapped = one("SELECT count(*) FROM events WHERE course_id IS NOT NULL")
    checked = one("SELECT count(DISTINCT event_id) FROM scrape_runs WHERE status <> 'loaded'")
    m.append(Metric("events_total", events))
    m.append(Metric("events_mapped_to_course_pct", _pct(mapped, events), mapped, events))
    m.append(Metric("events_checked_pct", _pct(checked, events), checked, events))

    riders = one("SELECT count(*) FROM riders")
    m.append(Metric("riders_total", riders))
    m.append(
        Metric("rider_alias_groups", one("SELECT count(DISTINCT canonical_id) FROM rider_aliases"))
    )
    m.append(
        Metric(
            "name_key_groups_unmerged",
            one("""
            SELECT count(*) FROM (
                SELECT ri.name_key
                FROM riders ri LEFT JOIN rider_aliases a ON a.rider_id = ri.id
                WHERE ri.name_key IS NOT NULL
                GROUP BY ri.name_key
                HAVING count(DISTINCT COALESCE(a.canonical_id, ri.id)) > 1
            ) x
            """),
        )
    )
    m.append(
        Metric(
            "conflict_name_groups",
            one("""
            SELECT count(DISTINCT k) FROM (
                SELECT COALESCE(ri.name_key, ri.name) AS k
                FROM riders ri JOIN results r ON r.rider_id = ri.id
                GROUP BY COALESCE(ri.name_key, ri.name), r.event_id
                HAVING count(DISTINCT ri.id) > 1
            ) x
            """),
        )
    )
    m.append(
        Metric(
            "team_variant_groups",
            one(
                "SELECT count(*) FROM (SELECT team_key FROM riders WHERE team_key IS NOT NULL "
                "GROUP BY team_key HAVING count(DISTINCT team) > 1) x"
            ),
        )
    )
    m.extend(_golden_metrics(session))

    if event_id is not None:
        ev_total = one("SELECT count(*) FROM results WHERE event_id = :e", e=event_id)
        ev_bad = one(
            "SELECT count(*) FROM results WHERE event_id = :e AND dq_status = 'excluded'",
            e=event_id,
        )
        m.append(Metric("event_excluded_pct", _pct(ev_bad, ev_total), ev_bad, ev_total))
        drop = one(
            "SELECT count(*) FROM dq_checks WHERE event_id = :e AND \"check\" = 'results_count_drop' "
            "AND run_id = (SELECT max(run_id) FROM dq_checks WHERE event_id = :e)",
            e=event_id,
        )
        m.append(Metric("event_results_count_drop", drop))
    return m


def latest_runs(session: Session, n: int = 2) -> list[dict[str, float | None]]:
    """Most recent scorecard runs as {metric: value}, newest first."""
    ids = [
        r[0]
        for r in session.execute(
            text(
                "SELECT run_id FROM picl_dq_metrics GROUP BY run_id ORDER BY min(captured_at) DESC LIMIT :n"
            ),
            {"n": n},
        ).all()
    ]
    out: list[dict[str, float | None]] = []
    for rid in ids:
        rows = session.execute(
            text("SELECT metric, value FROM picl_dq_metrics WHERE run_id = :r"), {"r": rid}
        ).all()
        out.append({k: v for k, v in rows})
    return out


def run_scorecard(
    session: Session, run_id: int | None = None, event_id: int | None = None
) -> tuple[int, bool, list[str]]:
    """Compute metrics, evaluate the gate against the previous run, persist.

    With `run_id` (a scrape run) the verdict is also written to scrape_runs
    and its status becomes published or blocked. Without one, a standalone
    scorecard run id is minted.
    """
    seed_golden_pairs_from_conflicts(session)
    metrics = compute_metrics(session, event_id)
    prev = latest_runs(session, 1)
    curr = {x.metric: x.value for x in metrics}
    passed, reasons = evaluate_gate(curr, prev[0] if prev else None)
    if run_id is None:
        run_id = session.execute(
            text("SELECT COALESCE(max(run_id), 0) + 1 FROM picl_dq_metrics")
        ).scalar_one()
        run_id = max(run_id, 1_000_000)  # keep standalone ids clear of scrape_runs ids
    rows = [
        {
            "run": run_id,
            "m": x.metric,
            "v": x.value,
            "n": x.numerator,
            "d": x.denominator,
            "detail": json.dumps(x.detail),
        }
        for x in metrics
    ]
    rows.append(
        {
            "run": run_id,
            "m": "gate_passed",
            "v": 1.0 if passed else 0.0,
            "n": None,
            "d": None,
            "detail": json.dumps({"reasons": reasons}),
        }
    )
    session.execute(
        text("""
        INSERT INTO picl_dq_metrics (run_id, metric, value, numerator, denominator, detail)
        VALUES (:run, :m, :v, :n, :d, CAST(:detail AS jsonb))
        """),
        rows,
    )
    if event_id is not None:
        session.execute(
            text("""
            UPDATE scrape_runs SET gate_passed = :p, gate_reasons = CAST(:r AS jsonb),
                   status = CASE WHEN :p THEN 'published' ELSE 'blocked' END, finished_at = now()
            WHERE id = :run
            """),
            {"p": passed, "r": json.dumps(reasons), "run": run_id},
        )
    session.commit()
    return run_id, passed, reasons
