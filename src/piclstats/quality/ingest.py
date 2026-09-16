"""One race through the whole pipeline: parse → load → check → gate."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.db.loader import load_event
from piclstats.quality import checks, scorecard
from piclstats.scraper.parser import parse_event


@dataclass
class IngestResult:
    raceresult_id: int
    event_id: int
    event_name: str
    rows: int
    created: bool
    excluded: int
    warned: int
    findings: dict[str, int]
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def published(self) -> bool:
        return self.passed


def ingest_event(
    session: Session, raceresult_id: int, season: int, order: int, *, source: str
) -> IngestResult:
    """Load one event and run the checks and the gate.

    A newly created event is published only if the gate passes. A re-scrape
    of an existing event never changes its published flag: the gate's
    per-event rules would otherwise hide a 2022 race whose known-bad rows
    are already excluded row by row.
    """
    event_results = parse_event(raceresult_id, season, order)
    stats = load_event(session, event_results)
    run_id = checks.start_run(
        session,
        raceresult_id=raceresult_id,
        season=season,
        event_id=stats.event_id,
        rows_parsed=len(event_results.results),
        rows_loaded=stats.results,
        riders_new=stats.riders_new,
        detail={"source": source},
    )
    summary = checks.run_checks(session, run_id, stats.event_id)
    _, passed, reasons = scorecard.run_scorecard(session, run_id=run_id, event_id=stats.event_id)
    if stats.created:
        session.execute(
            text("UPDATE events SET is_published = :p WHERE id = :id"),
            {"p": passed, "id": stats.event_id},
        )
        session.commit()
    return IngestResult(
        raceresult_id=raceresult_id,
        event_id=stats.event_id,
        event_name=event_results.config.event_name,
        rows=stats.results,
        created=stats.created,
        excluded=summary.excluded,
        warned=summary.warned,
        findings=summary.findings,
        passed=passed,
        reasons=reasons,
    )
