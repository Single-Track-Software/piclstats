"""One race through the whole pipeline: parse → load → check → gate."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlalchemy import text
from sqlalchemy.orm import Session

from piclstats.db.loader import load_event
from piclstats.quality import checks, scorecard
from piclstats.models import EventResults
from piclstats.quality.keys import name_key
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
    if "rally" in event_results.config.event_name.lower():
        fill_rally_teams(session, event_results)
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


_CONFERENCES = ("Eastern Gold", "Eastern Blue", "Central", "Western", "Eastern")


def fill_rally_teams(session: Session, event_results: EventResults) -> int:
    """Give rally rows a team from the rider's other races that season.

    A rally's segment lists carry no team, and riders are keyed by name and
    team (and conferences hang off the team). Each row is matched in the
    rally's own conference, named in the event, by category and name, then
    by category and bib (2026 reuses bibs across conferences). Returns how
    many rows got a team; the rest load teamless.
    """
    name = event_results.config.event_name
    conference = next((c for c in _CONFERENCES if f"{c} Conference" in name), None)
    rows = session.execute(
        text(r"""
        SELECT DISTINCT r.category, r.bib, ri.name, ri.team
        FROM results r
        JOIN events e ON e.id = r.event_id
        JOIN riders ri ON ri.id = r.rider_id
        JOIN team_conferences tc ON tc.team = ri.team AND tc.season = e.season
        WHERE e.season = :season AND ri.team IS NOT NULL
          AND (CAST(:conference AS text) IS NULL
               OR regexp_replace(tc.conference, '\s+', ' ', 'g') = :conference)
    """),
        {"season": event_results.season, "conference": conference},
    ).all()
    by_name: dict[tuple[str, str], set[str]] = {}
    by_bib: dict[tuple[str, int], set[str]] = {}
    for category, bib, rider_name, team in rows:
        by_name.setdefault((category, name_key(rider_name)), set()).add(team)
        if bib:
            by_bib.setdefault((category, bib), set()).add(team)
    filled = 0
    for i, r in enumerate(event_results.results):
        teams = by_name.get((r.category, name_key(r.name))) or by_bib.get((r.category, r.bib))
        if teams and len(teams) == 1:
            event_results.results[i] = replace(r, team=next(iter(teams)))
            filled += 1
    return filled
