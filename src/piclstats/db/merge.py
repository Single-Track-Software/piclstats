"""Rider merge/alias logic for deduplicating riders across teams."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from piclstats.db.engine import rowcount
from piclstats.db.tables import rider_aliases

logger = logging.getLogger(__name__)


def find_auto_merge_candidates(session: Session) -> list[dict]:
    """Find riders with the same name that never appear in the same event.

    These are safe to auto-merge (same person, different teams across seasons).
    Returns list of {name, rider_ids, teams, race_counts}.
    """
    rows = session.execute(
        text("""
        WITH dupe_names AS (
            SELECT name FROM riders GROUP BY name HAVING count(*) > 1
        ),
        conflicts AS (
            SELECT DISTINCT ri.name
            FROM riders ri
            JOIN results r ON r.rider_id = ri.id
            GROUP BY ri.name, r.event_id
            HAVING count(DISTINCT ri.id) > 1
        ),
        rider_counts AS (
            SELECT ri.id, ri.name, ri.team, count(r.id) AS races
            FROM riders ri
            JOIN results r ON r.rider_id = ri.id
            WHERE ri.name IN (SELECT name FROM dupe_names)
              AND ri.name NOT IN (SELECT name FROM conflicts)
            GROUP BY ri.id, ri.name, ri.team
        )
        SELECT name, id, team, races
        FROM rider_counts
        ORDER BY name, races DESC, id
    """)
    ).all()

    grouped: dict[str, dict] = {}
    for name, rid, team, races in rows:
        if name not in grouped:
            grouped[name] = {"name": name, "rider_ids": [], "teams": [], "race_counts": []}
        grouped[name]["rider_ids"].append(rid)
        grouped[name]["teams"].append(team)
        grouped[name]["race_counts"].append(races)

    return [v for v in grouped.values() if len(v["rider_ids"]) > 1]


def find_conflicts(session: Session) -> list[dict]:
    """Find riders with the same name that DO appear in the same event.

    These need manual review — likely different people with the same name.
    """
    rows = session.execute(
        text("""
        SELECT
            ri.name,
            ri.id,
            ri.team,
            count(r.id) AS races,
            array_agg(DISTINCT r.category ORDER BY r.category) AS categories,
            array_agg(DISTINCT e.season ORDER BY e.season) AS seasons
        FROM riders ri
        JOIN results r ON r.rider_id = ri.id
        JOIN events e ON r.event_id = e.id
        WHERE ri.name IN (
            SELECT ri2.name
            FROM riders ri2
            JOIN results r2 ON r2.rider_id = ri2.id
            GROUP BY ri2.name, r2.event_id
            HAVING count(DISTINCT ri2.id) > 1
        )
        GROUP BY ri.name, ri.id, ri.team
        ORDER BY ri.name, races DESC
    """)
    ).all()

    grouped: dict[str, dict] = {}
    for row in rows:
        name = row[0]
        if name not in grouped:
            grouped[name] = {"name": name, "entries": []}
        grouped[name]["entries"].append(
            {
                "rider_id": row[1],
                "team": row[2],
                "races": row[3],
                "categories": row[4],
                "seasons": row[5],
            }
        )

    return list(grouped.values())


def auto_merge(session: Session, dry_run: bool = False) -> int:
    """Auto-merge riders with the same name that never overlap in the same event.

    The rider with the most races becomes the canonical. All others become aliases.
    Returns the number of aliases created.
    """
    candidates = find_auto_merge_candidates(session)
    alias_count = 0

    for group in candidates:
        canonical_id = group["rider_ids"][0]  # most races
        alias_ids = group["rider_ids"][1:]

        if dry_run:
            logger.info(
                "Would merge %s: canonical=%d (%s, %d races), aliases=%s",
                group["name"],
                canonical_id,
                group["teams"][0],
                group["race_counts"][0],
                list(zip(alias_ids, group["teams"][1:], group["race_counts"][1:])),
            )
        else:
            for alias_id in alias_ids:
                stmt = (
                    insert(rider_aliases)
                    .values(
                        rider_id=alias_id,
                        canonical_id=canonical_id,
                        match_method="auto_name",
                    )
                    .on_conflict_do_update(
                        index_elements=["rider_id"],
                        set_={"canonical_id": canonical_id, "match_method": "auto_name"},
                    )
                )
                session.execute(stmt)
                alias_count += 1

    if not dry_run:
        session.commit()
        logger.info("Created %d aliases across %d rider groups", alias_count, len(candidates))

    return alias_count


def manual_merge(session: Session, canonical_id: int, alias_ids: list[int]) -> int:
    """Manually merge specific rider IDs under a canonical."""
    count = 0
    for alias_id in alias_ids:
        if alias_id == canonical_id:
            continue
        stmt = (
            insert(rider_aliases)
            .values(
                rider_id=alias_id,
                canonical_id=canonical_id,
                match_method="manual",
            )
            .on_conflict_do_update(
                index_elements=["rider_id"],
                set_={"canonical_id": canonical_id, "match_method": "manual"},
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def unmerge(session: Session, rider_id: int) -> bool:
    """Remove a rider from its canonical group."""
    result = session.execute(
        text("DELETE FROM rider_aliases WHERE rider_id = :id"), {"id": rider_id}
    )
    session.commit()
    return rowcount(result) > 0


def get_canonical_id(session: Session, rider_id: int) -> int:
    """Resolve a rider_id to its canonical ID (returns self if not aliased)."""
    row = session.execute(
        text("SELECT canonical_id FROM rider_aliases WHERE rider_id = :id"), {"id": rider_id}
    ).one_or_none()
    return row[0] if row else rider_id


def merge_stats(session: Session) -> dict:
    """Get current merge statistics."""
    row = session.execute(
        text("""
        SELECT
            (SELECT count(*) FROM rider_aliases) AS aliases,
            (SELECT count(DISTINCT canonical_id) FROM rider_aliases) AS canonical_groups,
            (SELECT count(*) FROM (
                SELECT name FROM riders GROUP BY name HAVING count(*) > 1
            ) x) AS remaining_dupes,
            (SELECT count(*) FROM riders) AS total_riders
    """)
    ).one()
    return {
        "aliases": row[0],
        "canonical_groups": row[1],
        "remaining_dupes": row[2],
        "total_riders": row[3],
    }
