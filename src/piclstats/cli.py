"""CLI entry point for piclstats."""

from __future__ import annotations

import logging
import sys

from typing import Any

import click

from piclstats.config import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
def main() -> None:
    """PICL Stats — PA mountain bike league results scraper."""
    _setup_logging()


@main.command()
def init_db() -> None:
    """Create/migrate the database schema via Alembic."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    click.echo("Database migrated to head.")


@main.command()
def seed() -> None:
    """Seed reference data (courses, conferences, division profiles)."""
    from piclstats.db.engine import get_session
    from piclstats.db.seed import seed_all

    import logging

    logging.basicConfig(level="INFO")

    session = get_session()
    try:
        seed_all(session)
        click.echo("Seed complete.")
    finally:
        session.close()


@main.command()
@click.option("--season", type=int, multiple=True, help="Season(s) to scrape (default: all).")
@click.option(
    "--event-id", type=int, multiple=True, help="Specific event ID(s). Overrides --season."
)
@click.option("--dry-run", is_flag=True, help="Scrape and parse only — do not write to DB.")
def scrape(season: tuple[int, ...], event_id: tuple[int, ...], dry_run: bool) -> None:
    """Scrape race results from raceresult.com and load into PostgreSQL."""
    from piclstats.scraper.parser import parse_event
    from piclstats.scraper.registry import get_events

    if event_id:
        # Explicit ids must be registered so a re-scrape keeps the event's
        # season and order; loading with (0, 0) used to overwrite both.
        from piclstats.scraper.registry import lookup_event

        targets = []
        for eid in event_id:
            found = lookup_event(eid)
            if found is None:
                raise click.ClickException(
                    f"event {eid} is not in scraper/registry.py SEASONS; add it there first"
                )
            targets.append((found[0], found[1], eid))
    else:
        targets = get_events(season if season else None)

    click.echo(f"Scraping {len(targets)} event(s)…")
    total_results = 0
    errors = 0

    # Imported lazily so --dry-run works without a reachable database.
    session = None
    if not dry_run:
        from piclstats.db.engine import get_session
        from piclstats.quality.ingest import ingest_event

        session = get_session()

    try:
        for s, order, eid in targets:
            try:
                if dry_run:
                    event_results = parse_event(eid, s, order)
                    total_results += len(event_results.results)
                    click.echo(
                        f"  [DRY RUN] Event {eid} ({event_results.config.event_name}): "
                        f"{len(event_results.results)} results parsed"
                    )
                    continue
                assert session is not None  # opened above whenever dry_run is False
                res = ingest_event(session, eid, s, order, source="scrape")
                total_results += res.rows
                click.echo(f"  Loaded event {eid} ({res.event_name}): {res.rows} results")
                click.echo(_ingest_line(res))
            except Exception as exc:
                errors += 1
                click.echo(f"  ERROR event {eid}: {exc}", err=True)
                logging.getLogger(__name__).debug("Event %d failed", eid, exc_info=True)
    finally:
        if session:
            session.close()

    click.echo(f"\nDone. {total_results} total results, {errors} error(s).")
    if errors:
        sys.exit(1)


@main.command()
@click.argument("kind", type=click.Choice(["rider", "team", "event", "stats"]))
@click.option("--name", help="Rider or team name to search (case-insensitive partial match).")
@click.option("--season", type=int, help="Filter by season.")
def query(kind: str, name: str | None, season: int | None) -> None:
    """Run quick queries against the database."""
    from sqlalchemy import func, select

    from piclstats.db.engine import get_session
    from piclstats.db.tables import events, results, riders

    session = get_session()

    try:
        if kind == "stats":
            row = session.execute(
                select(
                    func.count(results.c.id).label("total_results"),
                    func.count(func.distinct(riders.c.id)).label("unique_riders"),
                    func.count(func.distinct(events.c.id)).label("events"),
                ).select_from(
                    results.join(riders, results.c.rider_id == riders.c.id).join(
                        events, results.c.event_id == events.c.id
                    )
                )
            ).one()
            click.echo(
                f"Total results: {row.total_results}\n"
                f"Unique riders: {row.unique_riders}\n"
                f"Events: {row.events}"
            )

        elif kind == "rider":
            if not name:
                click.echo("--name required for rider query", err=True)
                sys.exit(1)
            stmt = (
                select(
                    riders.c.name,
                    riders.c.team,
                    events.c.season,
                    events.c.event_name,
                    results.c.category,
                    results.c.place,
                    results.c.points,
                    results.c.total_time_raw,
                )
                .select_from(
                    results.join(riders, results.c.rider_id == riders.c.id).join(
                        events, results.c.event_id == events.c.id
                    )
                )
                .where(riders.c.name.ilike(f"%{name}%"))
                .order_by(events.c.season, events.c.event_order)
            )
            if season:
                stmt = stmt.where(events.c.season == season)

            rows = session.execute(stmt).all()
            if not rows:
                click.echo("No results found.")
                return
            click.echo(
                f"{'Name':<25} {'Team':<25} {'Season':<7} {'Event':<30} {'Cat':<25} {'PLC':<5} {'PTS':<5} {'Time'}"
            )
            click.echo("-" * 150)
            for r in rows:
                click.echo(
                    f"{r.name:<25} {(r.team or ''):<25} {r.season:<7} {r.event_name:<30} "
                    f"{r.category:<25} {str(r.place or '-'):<5} {str(r.points or '-'):<5} {r.total_time_raw}"
                )

        elif kind == "team":
            if not name:
                click.echo("--name required for team query", err=True)
                sys.exit(1)
            stmt = (
                select(
                    riders.c.team,
                    events.c.season,
                    func.count(func.distinct(riders.c.id)).label("rider_count"),
                    func.avg(results.c.points).label("avg_points"),
                )
                .select_from(
                    results.join(riders, results.c.rider_id == riders.c.id).join(
                        events, results.c.event_id == events.c.id
                    )
                )
                .where(riders.c.team.ilike(f"%{name}%"))
                .group_by(riders.c.team, events.c.season)
                .order_by(events.c.season)
            )
            if season:
                stmt = stmt.where(events.c.season == season)

            rows = session.execute(stmt).all()
            if not rows:
                click.echo("No results found.")
                return
            click.echo(f"{'Team':<35} {'Season':<7} {'Riders':<8} {'Avg PTS'}")
            click.echo("-" * 60)
            for r in rows:
                avg = f"{r.avg_points:.1f}" if r.avg_points else "-"
                click.echo(f"{(r.team or ''):<35} {r.season:<7} {r.rider_count:<8} {avg}")

        elif kind == "event":
            stmt = (
                select(
                    events.c.season,
                    events.c.event_order,
                    events.c.event_name,
                    events.c.raceresult_id,
                    func.count(results.c.id).label("result_count"),
                )
                .select_from(events.outerjoin(results, results.c.event_id == events.c.id))
                .group_by(events.c.id)
                .order_by(events.c.season, events.c.event_order)
            )
            if season:
                stmt = stmt.where(events.c.season == season)

            rows = session.execute(stmt).all()
            if not rows:
                click.echo("No events found.")
                return
            click.echo(f"{'Season':<7} {'#':<3} {'Event':<40} {'RR ID':<10} {'Results'}")
            click.echo("-" * 75)
            for r in rows:
                click.echo(
                    f"{r.season:<7} {r.event_order:<3} {r.event_name:<40} "
                    f"{str(r.raceresult_id or '—'):<10} {r.result_count}"
                )

    finally:
        session.close()


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host.")
@click.option("--port", default=8000, type=int, help="Bind port.")
@click.option("--reload", "do_reload", is_flag=True, help="Auto-reload on code changes.")
def serve(host: str, port: int, do_reload: bool) -> None:
    """Start the web dashboard."""
    import uvicorn

    click.echo(f"Starting PICL Stats dashboard at http://{host}:{port}")
    uvicorn.run(
        "piclstats.web.app:app",
        host=host,
        port=port,
        reload=do_reload,
    )


def _ingest_line(res: Any) -> str:
    parts = ", ".join(f"{k} {v}" for k, v in sorted(res.findings.items())) or "clean"
    gate = "PASS" if res.passed else "BLOCKED: " + "; ".join(res.reasons)
    return f"    DQ: {res.excluded} excluded, {res.warned} flagged ({parts}); gate {gate}"


@main.command()
@click.option("--scrape", is_flag=True, help="Load every new race through the DQ pipeline.")
@click.option("--season", type=int, default=None, help="Season for new races (default: this year).")
@click.option(
    "--event-id",
    "event_ids",
    type=int,
    multiple=True,
    help="raceresult id(s) to treat as discovered, for races posted before the league page links them.",
)
def discover(scrape: bool, season: int | None, event_ids: tuple[int, ...]) -> None:
    """Check the league results page for races we have not loaded."""
    from piclstats.db.engine import get_session
    from piclstats.scraper import discover as disc

    if event_ids:
        source = "the command line"
        links = [disc.Discovered(eid, f"raceresult {eid}") for eid in event_ids]
    else:
        source = disc.RESULTS_URL
        links = disc.parse_links(disc.fetch_page())
    season = season or disc.current_season()
    session = get_session()
    try:
        new = disc.find_new(session, links)
        click.echo(f"{len(links)} race link(s) on {source}; {len(new)} new")
        for d in new:
            disc.record(session, d, season, "new")
        session.commit()
        if not new:
            return
        for d in new:
            click.echo(f"  {d.raceresult_id}  {d.name}")
        if not scrape:
            click.echo("re-run with --scrape to load them")
            return

        from piclstats.db.merge import auto_merge
        from piclstats.db.seed import seed_all
        from piclstats.quality.ingest import ingest_event
        from piclstats.quality.lineage import rebuild_rider_lineage

        loaded = 0
        for d in new:
            order = disc.next_event_order(session, season)
            try:
                res = ingest_event(session, d.raceresult_id, season, order, source="discover")
            except Exception as exc:  # keep going; the DQ page shows the failure
                session.rollback()
                disc.record(session, d, season, "failed", str(exc)[:500])
                session.commit()
                click.echo(f"  ERROR {d.raceresult_id} ({d.name}): {exc}", err=True)
                continue
            loaded += 1
            if res.event_name:  # an id given by hand had only a placeholder name
                d = disc.Discovered(d.raceresult_id, res.event_name)
            disc.record(
                session, d, season, "published" if res.passed else "blocked", "; ".join(res.reasons)
            )
            session.commit()
            click.echo(f"  Loaded {d.raceresult_id} ({res.event_name}): {res.rows} results")
            click.echo(_ingest_line(res))
        if loaded:
            seed_all(session)  # course mapping, season profiles, event types, conferences
            auto_merge(session)
            rebuild_rider_lineage(session)
            session.commit()
            click.echo("seeded profiles, merged riders, rebuilt lineage")
    finally:
        session.close()


def _summary_line(summary: Any) -> str:
    parts = ", ".join(f"{k} {v}" for k, v in sorted(summary.findings.items())) or "clean"
    return (
        f"    DQ run {summary.run_id}: {summary.excluded} excluded, {summary.warned} flagged"
        f" ({parts})"
    )


@main.command("forecast-backtest")
@click.option("--min-season", type=int, default=2024, help="Score forecasts from this season on.")
def forecast_backtest(min_season: int) -> None:
    """Replay past races through the future-race forecast and score it."""
    from piclstats.backtest import BacktestResult, run_backtest
    from piclstats.db.engine import get_session
    from piclstats.web import queries

    total = BacktestResult()
    with get_session() as session:
        for gender in ("Male", "Female"):
            for loop_type in ("HS", "MS"):
                rows = queries.rating_rows(session, gender, loop_type)
                total.merge(run_backtest(rows, min_season=min_season))
    click.echo(total.report())


@main.group()
def dq() -> None:
    """Data-quality checks (ADR 002)."""


@dq.command("check")
@click.option("--event-id", "event_ids", multiple=True, type=int, help="events.id to check")
@click.option("--all", "check_all", is_flag=True, help="Check every loaded event.")
def dq_check(event_ids: tuple[int, ...], check_all: bool) -> None:
    """Run the row checks over loaded events and roll up results.dq_status."""
    from sqlalchemy import text

    from piclstats.db.engine import get_session
    from piclstats.quality import checks

    session = get_session()
    try:
        if check_all:
            ids = [
                r[0]
                for r in session.execute(
                    text("SELECT id FROM events ORDER BY season, event_order")
                ).all()
            ]
        else:
            ids = list(event_ids)
        if not ids:
            raise click.ClickException("give --event-id N (repeatable) or --all")
        totals: dict[str, int] = {}
        excluded = warned = 0
        for eid in ids:
            summary = checks.check_event(session, eid)
            excluded += summary.excluded
            warned += summary.warned
            for k, v in summary.findings.items():
                totals[k] = totals.get(k, 0) + v
            click.echo(f"event {eid}: {_summary_line(summary).strip()}")
        click.echo(f"{len(ids)} events: {excluded} excluded, {warned} flagged")
        for k, v in sorted(totals.items()):
            click.echo(f"  {k}: {v}")
    finally:
        session.close()


@dq.command("scorecard")
def dq_scorecard() -> None:
    """Compute the scorecard, evaluate the gate against the previous run, record both."""
    from piclstats.db.engine import get_session
    from piclstats.quality import scorecard

    session = get_session()
    try:
        run_id, passed, reasons = scorecard.run_scorecard(session)
        for m in scorecard.compute_metrics(session):
            frac = f" ({m.numerator}/{m.denominator})" if m.denominator else ""
            click.echo(f"  {m.metric:34} {m.value if m.value is not None else '—'}{frac}")
        click.echo(
            f"run {run_id}: gate " + ("PASS" if passed else "BLOCKED: " + "; ".join(reasons))
        )
    finally:
        session.close()


@dq.command("lineage")
def dq_lineage() -> None:
    """Rebuild the lineage log (rider merges, team/division/event/conference folds)."""
    from piclstats.db.engine import get_session
    from piclstats.quality.lineage import rebuild_all

    session = get_session()
    try:
        runs = rebuild_all(session)
        for level, run_id in runs.items():
            click.echo(f"{level:11} run {run_id}")
    finally:
        session.close()


@dq.command("status")
def dq_status() -> None:
    """Counts of results by dq_status and the last few scrape runs."""
    from sqlalchemy import text

    from piclstats.db.engine import get_session

    session = get_session()
    try:
        for status, n in session.execute(
            text("SELECT dq_status, count(*) FROM results GROUP BY 1 ORDER BY 1")
        ).all():
            click.echo(f"{status:9} {n}")
        click.echo("recent runs:")
        for row in session.execute(
            text("""
            SELECT r.id, r.raceresult_id, r.season, r.status, r.rows_loaded, r.started_at
            FROM scrape_runs r ORDER BY r.id DESC LIMIT 10
            """)
        ).all():
            click.echo("  " + " ".join(str(x) for x in row))
    finally:
        session.close()


@main.group()
def merge() -> None:
    """Manage rider deduplication/merging."""


@merge.command("auto")
@click.option("--dry-run", is_flag=True, help="Show what would be merged without doing it.")
def merge_auto(dry_run: bool) -> None:
    """Auto-merge riders with the same name (no same-event overlap)."""
    from piclstats.db.engine import get_session
    from piclstats.db.merge import auto_merge

    session = get_session()
    try:
        if dry_run:
            import logging

            logging.basicConfig(level="INFO")
        count = auto_merge(session, dry_run=dry_run)
        if dry_run:
            click.echo(f"\nDry run complete. Would create {count} aliases.")
        else:
            click.echo(f"Created {count} aliases.")
            from piclstats.quality.lineage import rebuild_rider_lineage

            rebuild_rider_lineage(session)
            session.commit()
    finally:
        session.close()


@merge.command("status")
def merge_status() -> None:
    """Show merge statistics."""
    from piclstats.db.engine import get_session
    from piclstats.db.merge import merge_stats

    session = get_session()
    try:
        stats = merge_stats(session)
        click.echo(
            f"Total riders: {stats['total_riders']}\n"
            f"Aliases: {stats['aliases']}\n"
            f"Canonical groups: {stats['canonical_groups']}\n"
            f"Remaining duplicate names: {stats['remaining_dupes']}"
        )
    finally:
        session.close()


@merge.command("conflicts")
def merge_conflicts() -> None:
    """Show riders with same name that overlap in events (need manual review)."""
    from piclstats.db.engine import get_session
    from piclstats.db.merge import find_conflicts

    session = get_session()
    try:
        conflicts = find_conflicts(session)
        if not conflicts:
            click.echo("No conflicts found.")
            return
        for group in conflicts:
            click.echo(f"\n{group['name']}:")
            for e in group["entries"]:
                click.echo(
                    f"  id={e['rider_id']:<5} team={e['team']:<35} "
                    f"races={e['races']:<3} cats={e['categories']} seasons={e['seasons']}"
                )
    finally:
        session.close()


@merge.command("link")
@click.argument("canonical_id", type=int)
@click.argument("alias_ids", type=int, nargs=-1, required=True)
def merge_link(canonical_id: int, alias_ids: tuple[int, ...]) -> None:
    """Manually merge rider IDs under a canonical ID."""
    from piclstats.db.engine import get_session
    from piclstats.db.merge import manual_merge

    session = get_session()
    try:
        count = manual_merge(session, canonical_id, list(alias_ids))
        click.echo(f"Linked {count} alias(es) to canonical rider {canonical_id}.")
    finally:
        session.close()


@merge.command("unlink")
@click.argument("rider_id", type=int)
def merge_unlink(rider_id: int) -> None:
    """Remove a rider from its merged group."""
    from piclstats.db.engine import get_session
    from piclstats.db.merge import unmerge

    session = get_session()
    try:
        if unmerge(session, rider_id):
            click.echo(f"Rider {rider_id} unlinked.")
        else:
            click.echo(f"Rider {rider_id} was not merged.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
