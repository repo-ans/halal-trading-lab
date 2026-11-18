"""Stage D: execution_ts on signals, gate-snapshot/blocked-instrument columns on
strategy_runs.

signals.execution_ts: the next calendar-expected bar open after the signal bar,
within the same trading session (CLAUDE.md section 7) -- missing from the original
Stage A scaffold, which predates the engine that needed it.

strategy_runs.compliance_check_ids / blocked_instruments: the Stage D/E determinism
note (CLAUDE.md section 7) requires a run to snapshot which compliance_checks.id it
used per instrument (JSONB, instrument_id -> check id) so a replay stays
reproducible even if the real record expires between runs, and to report
per-instrument compliance-block reasons (JSONB, instrument_id -> reason) without
failing instruments that ARE compliant.

strategy_runs.reset_ma_each_session: one of the four unconfirmed-assumption config
flags (CLAUDE.md section 7) -- was missing a column entirely.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # signals has never been written to before Stage D (strategy_runs/signals were
    # scaffolded in Stage A but unused through Stage C) -- NOT NULL with no existing
    # rows to backfill.
    op.add_column("signals", sa.Column("execution_ts", sa.DateTime(timezone=True), nullable=False))
    op.add_column("strategy_runs", sa.Column(
        "blocked_instruments", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        server_default="{}"))
    op.add_column("strategy_runs", sa.Column(
        "compliance_check_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        server_default="{}"))
    op.add_column("strategy_runs", sa.Column(
        "reset_ma_each_session", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("strategy_runs", "reset_ma_each_session")
    op.drop_column("strategy_runs", "compliance_check_ids")
    op.drop_column("strategy_runs", "blocked_instruments")
    op.drop_column("signals", "execution_ts")
