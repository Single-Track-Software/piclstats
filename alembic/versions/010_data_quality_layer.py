"""Data-quality layer: runs, checks, lineage, scorecard, golden fixtures

Revision ID: 010
Revises: 009
Create Date: 2026-09-16

ADR 002. Adds the derived blocking keys on riders (name_key, team_key) and
results.dq_status, plus the seven DQ tables. Keys are backfilled with
quality.keys so existing and future rows use one rule.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from piclstats.quality.keys import name_key, team_key

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("riders", sa.Column("name_key", sa.Text))
    op.add_column("riders", sa.Column("team_key", sa.Text))
    op.create_index("idx_riders_name_key", "riders", ["name_key"])
    # Backfill with the Python rule so accents fold ("ZOË" -> "ZOE") exactly as
    # the loader will key new riders; ~2.5k rows, one statement each.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name, team FROM riders")).all()
    for rid, name, team in rows:
        bind.execute(
            sa.text("UPDATE riders SET name_key = :nk, team_key = :tk WHERE id = :id"),
            {"nk": name_key(name), "tk": team_key(team), "id": rid},
        )

    op.add_column(
        "results",
        sa.Column("dq_status", sa.Text, nullable=False, server_default="ok"),
    )
    op.create_index(
        "idx_results_dq_status",
        "results",
        ["dq_status"],
        postgresql_where=sa.text("dq_status <> 'ok'"),
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer),
        sa.Column("raceresult_id", sa.Integer, nullable=False),
        sa.Column("season", sa.SmallInteger),
        sa.Column("started_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", _TS),
        sa.Column("status", sa.Text, nullable=False, server_default="loaded"),
        sa.Column("rows_parsed", sa.Integer),
        sa.Column("rows_loaded", sa.Integer),
        sa.Column("riders_new", sa.Integer),
        sa.Column("gate_passed", sa.Boolean),
        sa.Column("gate_reasons", JSONB),
        sa.Column("detail", JSONB),
    )
    op.create_index("idx_scrape_runs_event", "scrape_runs", ["raceresult_id", "started_at"])

    op.create_table(
        "dq_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("event_id", sa.Integer, nullable=False),
        sa.Column("result_id", sa.Integer),
        sa.Column("check", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("observed", sa.Text),
        sa.Column("expected", sa.Text),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_dq_checks_run", "dq_checks", ["run_id"])
    op.create_index("idx_dq_checks_result", "dq_checks", ["result_id"])
    op.create_index("idx_dq_checks_check", "dq_checks", ["check"])

    op.create_table(
        "picl_lineage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("canonical_key", sa.Text, nullable=False),
        sa.Column("raw_value", sa.Text, nullable=False),
        sa.Column("raw_id", sa.Integer),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("origin", sa.Text),
        sa.Column("mechanism", sa.Text, nullable=False),
        sa.Column("match_score", sa.Float),
        sa.Column("volume", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_lineage_level_key", "picl_lineage", ["level", "canonical_key"])
    op.create_index("idx_lineage_run", "picl_lineage", ["run_id"])
    op.create_index("idx_lineage_mechanism", "picl_lineage", ["mechanism"])

    op.create_table(
        "picl_lineage_runs",
        sa.Column("run_id", sa.Integer, primary_key=True),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("edges", sa.Integer, nullable=False),
        sa.Column("canonicals", sa.Integer, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "picl_dq_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("value", sa.Float),
        sa.Column("numerator", sa.Integer),
        sa.Column("denominator", sa.Integer),
        sa.Column("detail", JSONB),
        sa.Column("captured_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_dq_metrics_metric", "picl_dq_metrics", ["metric", "captured_at"])
    op.create_index("idx_dq_metrics_run", "picl_dq_metrics", ["run_id"])

    op.create_table(
        "picl_golden",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("subject", JSONB, nullable=False),
        sa.Column("expected", JSONB, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "picl_golden_pairs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rider_id_a", sa.Integer, nullable=False),
        sa.Column("rider_id_b", sa.Integer, nullable=False),
        sa.Column("should_merge", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("note", sa.Text),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("rider_id_a", "rider_id_b", name="uq_golden_pair"),
    )


def downgrade() -> None:
    for table in (
        "picl_golden_pairs",
        "picl_golden",
        "picl_dq_metrics",
        "picl_lineage_runs",
        "picl_lineage",
        "dq_checks",
        "scrape_runs",
    ):
        op.drop_table(table)
    op.drop_index("idx_results_dq_status", table_name="results")
    op.drop_column("results", "dq_status")
    op.drop_index("idx_riders_name_key", table_name="riders")
    op.drop_column("riders", "team_key")
    op.drop_column("riders", "name_key")
