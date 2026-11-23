"""Replay Trading: replay_sessions, replay_fills.

New feature (CLAUDE.md section 1 / the "Simul8or"-style screen, decided
2026-11-23): a human clicks Buy/Sell against a replaying chart of already-
recorded historical bars, with a live dummy balance. Deliberately separate
from strategy_runs/paper_orders/paper_fills (Stage D/E) -- those are the
automated-backtest/determinism machinery; this is a single manual session
with no determinism requirement. Reuses price_bars for chart data (no new
price source) and the same compliance-gate/cost-model/EOD-exit rules as
Stage E, just invoked interactively instead of in a batch.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)
TS = sa.DateTime(timezone=True)


def pk():
    return sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "replay_sessions",
        pk(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.BigInteger,
                  sa.ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_day", sa.Date, nullable=False),
        sa.Column("starting_cash", MONEY, nullable=False, server_default="1000"),
        sa.Column("max_position_value", MONEY, nullable=False, server_default="200"),
        sa.Column("commission_per_order", MONEY, nullable=False, server_default="1.00"),
        sa.Column("spread_bps", MONEY, nullable=False, server_default="2"),
        sa.Column("slippage_bps", MONEY, nullable=False, server_default="1"),
        sa.Column("compliance_check_id", sa.BigInteger,
                  sa.ForeignKey("compliance_checks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.CheckConstraint("status IN ('active','completed')", name="ck_replay_session_status"),
    )
    op.create_index("ix_replay_sessions_user", "replay_sessions", ["user_id", "started_at"])

    op.create_table(
        "replay_fills",
        pk(),
        sa.Column("replay_session_id", sa.BigInteger,
                  sa.ForeignKey("replay_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin", sa.Text, nullable=False),  # 'manual' | 'eod_exit'
        sa.Column("side", sa.Text, nullable=False),    # 'buy' | 'sell'
        sa.Column("fill_ts", TS, nullable=False),
        sa.Column("fill_price", MONEY, nullable=False),
        sa.Column("qty", MONEY, nullable=False),
        sa.Column("commission", MONEY, nullable=False),
        sa.Column("spread_cost", MONEY, nullable=False),
        sa.Column("slippage_cost", MONEY, nullable=False),
        sa.Column("cash_balance_after", MONEY, nullable=False),
        sa.Column("realized_pnl", MONEY),  # only set on a 'sell' fill
        sa.CheckConstraint("origin IN ('manual','eod_exit')", name="ck_replay_fill_origin"),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_replay_fill_side"),
        sa.CheckConstraint("qty > 0", name="ck_replay_fill_qty_positive"),
        sa.CheckConstraint("cash_balance_after >= 0", name="ck_replay_fill_cash_nonneg"),
    )
    op.create_index("ix_replay_fills_session", "replay_fills", ["replay_session_id", "id"])

    # Migration 0002's deny-by-default posture (CLAUDE.md section 6): RLS ON, no
    # policies, for every table -- these two are no exception just because
    # they're new. Supabase exposes the public schema via PostgREST with the
    # anon key; without this, anyone holding that key could write straight to
    # replay_fills and fabricate a cash balance, bypassing the engine entirely.
    op.execute("ALTER TABLE replay_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE replay_fills ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE replay_fills DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE replay_sessions DISABLE ROW LEVEL SECURITY")
    op.drop_table("replay_fills")
    op.drop_table("replay_sessions")
