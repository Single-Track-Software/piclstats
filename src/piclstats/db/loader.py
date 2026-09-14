"""Load parsed results into PostgreSQL with upsert semantics."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from piclstats.db.tables import events, results, riders
from piclstats.models import EventResults

logger = logging.getLogger(__name__)


def _td_to_interval(td: timedelta | None) -> float | None:
    """Convert timedelta to total seconds for Interval storage."""
    if td is None:
        return None
    return td.total_seconds()


def load_event(session: Session, event_results: EventResults) -> int:
    """Upsert a full event's results. Returns count of results loaded."""
    config = event_results.config

    # 1. Upsert event
    evt_stmt = (
        insert(events)
        .values(
            raceresult_id=config.raceresult_id,
            season=event_results.season,
            event_name=config.event_name,
            event_order=event_results.event_order,
        )
        .on_conflict_do_update(
            index_elements=["raceresult_id"],
            set_={
                "season": event_results.season,
                "event_name": config.event_name,
                "event_order": event_results.event_order,
            },
        )
        .returning(events.c.id)
    )

    event_id = session.execute(evt_stmt).scalar_one()

    # 2. Batch upsert riders, build lookup
    unique_riders: dict[tuple[str, str | None], dict] = {}
    for r in event_results.results:
        key = (r.name, r.team)
        if key not in unique_riders:
            unique_riders[key] = {"name": r.name, "team": r.team, "school": r.school}
        elif r.school and not unique_riders[key].get("school"):
            unique_riders[key]["school"] = r.school

    rider_lookup: dict[tuple[str, str | None], int] = {}
    for rider_key, vals in unique_riders.items():
        rider_stmt = (
            insert(riders)
            .values(**vals)
            .on_conflict_do_update(
                constraint="uq_riders_name_team",
                set_={"school": vals["school"]} if vals["school"] else {"name": vals["name"]},
            )
            .returning(riders.c.id)
        )
        rider_id = session.execute(rider_stmt).scalar_one()
        rider_lookup[rider_key] = rider_id

    # 3. Batch upsert results
    count = 0
    for r in event_results.results:
        rider_id = rider_lookup[(r.name, r.team)]
        result_vals = {
            "event_id": event_id,
            "rider_id": rider_id,
            "bib": r.bib,
            "category": r.category,
            "category_order": r.category_order,
            "gender": r.gender,
            "division": r.division,
            "place": r.place,
            "status": r.status,
            "points": r.points,
            "conference": r.conference,
            "lap1": r.lap1,
            "lap2": r.lap2,
            "lap3": r.lap3,
            "lap4": r.lap4,
            "lap5": r.lap5,
            "lap6": r.lap6,
            "penalty": r.penalty,
            "total_time": r.total_time,
            "total_time_raw": r.total_time_raw,
            "raw_data": json.dumps(r.raw_row),
        }
        update_vals = {k: v for k, v in result_vals.items() if k not in ("event_id", "bib")}

        res_stmt = (
            insert(results)
            .values(**result_vals)
            .on_conflict_do_update(
                constraint="uq_results_event_bib",
                set_=update_vals,
            )
        )
        session.execute(res_stmt)
        count += 1

    session.commit()
    logger.info(
        "Loaded event %d (%s): %d results, %d unique riders",
        config.raceresult_id,
        config.event_name,
        count,
        len(rider_lookup),
    )
    return count
