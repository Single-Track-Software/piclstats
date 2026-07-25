"""CLI entry point for piclstats."""

from __future__ import annotations

import logging
import sys

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
        # Build (season, order, id) for explicit event IDs — season/order unknown
        targets = [(0, 0, eid) for eid in event_id]
    else:
        targets = get_events(season if season else None)

    click.echo(f"Scraping {len(targets)} event(s)…")
    total_results = 0
    errors = 0

    session = None
    load_event = None
    if not dry_run:
        from piclstats.db.engine import get_session
        from piclstats.db.loader import load_event

        session = get_session()

    try:
        for s, order, eid in targets:
            try:
                event_results = parse_event(eid, s, order)
                n = len(event_results.results)
                total_results += n

                if dry_run:
                    click.echo(
                        f"  [DRY RUN] Event {eid} ({event_results.config.event_name}): "
                        f"{n} results parsed"
                    )
                else:
                    load_event(session, event_results)
                    click.echo(
                        f"  Loaded event {eid} ({event_results.config.event_name}): {n} results"
                    )
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
                    f"{r.raceresult_id:<10} {r.result_count}"
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
