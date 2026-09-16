"""Lineage: the decision log of every fold from a raw value to a canonical one.

Modelled on gvpd's merge map. Each rebuild writes a new run per level into
``picl_lineage`` (append-only; the inspector reads the latest run per level)
plus a summary row in ``picl_lineage_runs``. Levels:

- ``rider``: every rider row that feeds a canonical rider (via rider_aliases),
  including the canonical's own row as an ``exact`` edge.
- ``team``: raw team spellings that share a team_key.
- ``division``: DIVISION_ALIASES folds (Middle School Advanced -> MS Advanced).
- ``event``: event_name -> event_type and event_name -> course, both by pattern.
- ``conference``: raw conference labels -> conference_group.
"""

from __future__ import annotations

import difflib
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.quality.keys import name_key

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def classify_mechanism(raw: str, canonical: str) -> tuple[str, float]:
    """How `raw` maps onto `canonical`: (mechanism, similarity 0-1).

    exact > casing > whitespace > punctuation > typo, checking the cheapest
    explanation first so O'REILLY/OREILLY reads as punctuation, not typo.
    """
    if raw == canonical:
        return "exact", 1.0
    if raw.upper() == canonical.upper():
        return "casing", 1.0
    if _WS.sub(" ", raw).strip().upper() == _WS.sub(" ", canonical).strip().upper():
        return "whitespace", 1.0
    if name_key(raw) == name_key(canonical):
        return "punctuation", 1.0
    score = difflib.SequenceMatcher(None, raw.upper(), canonical.upper()).ratio()
    return "typo", round(score, 4)


def _next_run_id(session: Session) -> int:
    return session.execute(
        text("SELECT COALESCE(max(run_id), 0) + 1 FROM picl_lineage_runs")
    ).scalar_one()


def _write(session: Session, level: str, edges: list[dict]) -> int:
    """Insert one run's edges for `level` and its summary row; return run id."""
    run_id = _next_run_id(session)
    for e in edges:
        e["run_id"] = run_id
        e["level"] = level
    if edges:
        session.execute(
            text("""
            INSERT INTO picl_lineage
                (run_id, stage, level, canonical_key, raw_value, raw_id, source, origin,
                 mechanism, match_score, volume)
            VALUES (:run_id, :stage, :level, :canonical_key, :raw_value, :raw_id, :source,
                    :origin, :mechanism, :match_score, :volume)
            """),
            edges,
        )
    session.execute(
        text("""
        INSERT INTO picl_lineage_runs (run_id, level, edges, canonicals)
        VALUES (:run, :level, :edges, :canonicals)
        """),
        {
            "run": run_id,
            "level": level,
            "edges": len(edges),
            "canonicals": len({e["canonical_key"] for e in edges}),
        },
    )
    return run_id


def rebuild_rider_lineage(session: Session) -> int:
    rows = session.execute(
        text("""
        WITH races AS (SELECT rider_id, count(*) AS n FROM results GROUP BY rider_id)
        SELECT a.rider_id, ri.name, ri.team, a.canonical_id, c.name, a.match_method,
               COALESCE(rc.n, 0)
        FROM rider_aliases a
        JOIN riders ri ON ri.id = a.rider_id
        JOIN riders c ON c.id = a.canonical_id
        LEFT JOIN races rc ON rc.rider_id = a.rider_id
        ORDER BY a.canonical_id, a.rider_id
        """)
    ).all()
    edges: list[dict] = []
    canonicals: set[int] = set()
    for rid, name, team, cid, cname, method, n in rows:
        mech, score = ("manual", 1.0) if method == "manual" else classify_mechanism(name, cname)
        edges.append(
            {
                "stage": "rider_merge",
                "canonical_key": str(cid),
                "raw_value": f"{name} ({team or 'no team'})",
                "raw_id": rid,
                "source": "raceresult",
                "origin": "db/merge.py",
                "mechanism": mech,
                "match_score": score,
                "volume": n,
            }
        )
        canonicals.add(cid)
    if canonicals:
        for cid, cname, team, n in session.execute(
            text("""
            SELECT ri.id, ri.name, ri.team, (SELECT count(*) FROM results WHERE rider_id = ri.id)
            FROM riders ri WHERE ri.id = ANY(:ids)
            """),
            {"ids": list(canonicals)},
        ).all():
            edges.append(
                {
                    "stage": "rider_merge",
                    "canonical_key": str(cid),
                    "raw_value": f"{cname} ({team or 'no team'})",
                    "raw_id": cid,
                    "source": "raceresult",
                    "origin": "db/merge.py",
                    "mechanism": "exact",
                    "match_score": 1.0,
                    "volume": n,
                }
            )
    return _write(session, "rider", edges)


def rebuild_team_lineage(session: Session) -> int:
    rows = session.execute(
        text("""
        SELECT team_key, team, count(*) AS riders
        FROM riders WHERE team_key IS NOT NULL
        GROUP BY team_key, team
        ORDER BY team_key, riders DESC, team
        """)
    ).all()
    by_key: dict[str, list[tuple[str, int]]] = {}
    for key, team, n in rows:
        by_key.setdefault(key, []).append((team, n))
    edges: list[dict] = []
    for key, variants in by_key.items():
        if len(variants) < 2:
            continue
        canonical = variants[0][0]  # the most used spelling
        for team, n in variants:
            mech, score = classify_mechanism(team, canonical)
            edges.append(
                {
                    "stage": "team_norm",
                    "canonical_key": canonical,
                    "raw_value": team,
                    "raw_id": None,
                    "source": "raceresult",
                    "origin": "quality/keys.py:team_key",
                    "mechanism": mech,
                    "match_score": score,
                    "volume": n,
                }
            )
    return _write(session, "team", edges)


def rebuild_seed_lineage(session: Session) -> dict[str, int]:
    """Division, event and conference folds performed by db/seed.py."""
    from piclstats.db.seed import CONFERENCE_LINEAGE, COURSES, DIVISION_ALIASES

    div_edges: list[dict] = []
    for alias, canonical in DIVISION_ALIASES.items():
        n = session.execute(
            text("SELECT count(*) FROM results WHERE category ILIKE :pat"),
            {"pat": f"%{alias}%"},
        ).scalar_one()
        div_edges.append(
            {
                "stage": "division_norm",
                "canonical_key": canonical,
                "raw_value": alias,
                "raw_id": None,
                "source": "raceresult",
                "origin": "db/seed.py:DIVISION_ALIASES",
                "mechanism": "alias",
                "match_score": 1.0,
                "volume": n,
            }
        )

    event_edges: list[dict] = []
    for eid, name, etype, course, n in session.execute(
        text("""
        SELECT e.id, e.event_name, e.event_type, c.name,
               (SELECT count(*) FROM results WHERE event_id = e.id)
        FROM events e LEFT JOIN courses c ON c.id = e.course_id
        ORDER BY e.season, e.event_order
        """)
    ).all():
        event_edges.append(
            {
                "stage": "event_classify",
                "canonical_key": etype,
                "raw_value": name,
                "raw_id": eid,
                "source": "raceresult",
                "origin": "db/seed.py:classify_event_types",
                "mechanism": "pattern",
                "match_score": 1.0,
                "volume": n,
            }
        )
        if course:
            pats = COURSES.get(course, {}).get("patterns", [])
            hit = next((p for p in pats if p.lower() in name.lower()), None)
            event_edges.append(
                {
                    "stage": "course_map",
                    "canonical_key": course,
                    "raw_value": name,
                    "raw_id": eid,
                    "source": "raceresult",
                    "origin": "db/seed.py:map_events_to_courses",
                    "mechanism": "pattern" if hit else "manual",
                    "match_score": 1.0,
                    "volume": n,
                }
            )

    conf_edges: list[dict] = []
    for raw, group, n in session.execute(
        text("""
        SELECT r.conference, tc.conference_group, count(*)
        FROM results r
        JOIN riders ri ON ri.id = r.rider_id
        JOIN events e ON e.id = r.event_id
        JOIN team_conferences tc ON tc.team = ri.team AND tc.season = e.season
        WHERE r.conference IS NOT NULL AND r.conference <> ''
        GROUP BY r.conference, tc.conference_group
        """)
    ).all():
        mech, score = classify_mechanism(raw, group or raw)
        if raw.strip() in CONFERENCE_LINEAGE and mech == "typo":
            mech, score = "alias", 1.0
        conf_edges.append(
            {
                "stage": "conference_norm",
                "canonical_key": group or raw,
                "raw_value": raw,
                "raw_id": None,
                "source": "raceresult",
                "origin": "db/seed.py:CONFERENCE_LINEAGE",
                "mechanism": mech,
                "match_score": score,
                "volume": n,
            }
        )

    return {
        "division": _write(session, "division", div_edges),
        "event": _write(session, "event", event_edges),
        "conference": _write(session, "conference", conf_edges),
    }


def rebuild_all(session: Session) -> dict[str, int]:
    runs = {"rider": rebuild_rider_lineage(session), "team": rebuild_team_lineage(session)}
    runs.update(rebuild_seed_lineage(session))
    session.commit()
    return runs
