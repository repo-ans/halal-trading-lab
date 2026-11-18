"""initial schema: 8 tables

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# All money/price columns are NUMERIC(18,4). All timestamps are TIMESTAMPTZ (UTC).
MONEY = sa.Numeric(18, 4)
TS = sa.DateTime(timezone=True)


def pk():
    return sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True)


def upgrade() -> None:
    # 1. instruments
    op.create_table(
        "instruments",
        pk(),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("exchange", sa.Text, nullable=False),          # MIC code, e.g. XNAS
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "exchange", name="uq_instruments_symbol_exchange"),
    )

    # 2. compliance_checks (history + one current row per instrument)
    op.create_table(
        "compliance_checks",
        pk(),
        sa.Column("instrument_id", sa.BigInteger,
                  sa.ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.Text),
        sa.Column("evidence_file_path", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("checked_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),             # app default: checked_at + 90d
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),  # Supabase auth user id
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('halal','not_halal','doubtful','not_covered')",
            name="ck_compliance_status"),
        sa.CheckConstraint(
            "source IN ('manual','zoya','musaffa')", name="ck_compliance_source"),
        sa.CheckConstraint("expires_at > checked_at", name="ck_compliance_expiry_after_check"),
    )
    op.create_index(
        "uq_compliance_one_current", "compliance_checks", ["instrument_id"],
        unique=True, postgresql_where=sa.text("is_current"))
    op.create_index("ix_compliance_instrument_checked", "compliance_checks",
                    ["instrument_id", "checked_at"])

    # 3. price_bars
    op.create_table(
        "price_bars",
        pk(),
        sa.Column("instrument_id", sa.BigInteger,
                  sa.ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ts", TS, nullable=False),                     # bar OPEN time, UTC
        sa.Column("interval", sa.Text, nullable=False),          # e.g. '5m'
        sa.Column("open", MONEY, nullable=False),
        sa.Column("high", MONEY, nullable=False),
        sa.Column("low", MONEY, nullable=False),
        sa.Column("close", MONEY, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False),      # share count, not money
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("ingested_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instrument_id", "ts", "interval", name="uq_price_bars_idempotent"),
        sa.CheckConstraint("high >= low", name="ck_bar_high_ge_low"),
    )
    # Backtest query shape: WHERE instrument_id=? AND interval=? AND ts BETWEEN ? AND ?
    op.create_index("ix_price_bars_backtest", "price_bars", ["instrument_id", "interval", "ts"])

    # 4. strategy_runs
    op.create_table(
        "strategy_runs",
        pk(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),       # single-currency sim, no FX
        sa.Column("instrument_ids", postgresql.JSONB, nullable=False),   # sorted list of ids
        sa.Column("range_start", TS, nullable=False),
        sa.Column("range_end", TS, nullable=False),
        sa.Column("fast_period", sa.Integer, nullable=False, server_default="5"),
        sa.Column("slow_period", sa.Integer, nullable=False, server_default="20"),
        sa.Column("starting_cash", MONEY, nullable=False, server_default="1000"),
        sa.Column("max_position_value", MONEY, nullable=False, server_default="200"),
        sa.Column("commission_per_order", MONEY, nullable=False),
        sa.Column("spread_bps", MONEY, nullable=False),
        sa.Column("slippage_bps", MONEY, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),         # sha256 of config
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("error", sa.Text),
        sa.CheckConstraint("fast_period < slow_period", name="ck_ma_periods"),
        sa.CheckConstraint("range_end > range_start", name="ck_run_range"),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="ck_run_status"),
    )
    op.create_index("ix_strategy_runs_user", "strategy_runs", ["user_id", "started_at"])
    op.create_index("ix_strategy_runs_hash", "strategy_runs", ["config_hash"])

    # 5. signals
    op.create_table(
        "signals",
        pk(),
        sa.Column("strategy_run_id", sa.BigInteger,
                  sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", sa.BigInteger,
                  sa.ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("signal_ts", TS, nullable=False),              # the bar that produced it
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("ma_fast", MONEY, nullable=False),
        sa.Column("ma_slow", MONEY, nullable=False),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_signal_side"),
        sa.UniqueConstraint("strategy_run_id", "instrument_id", "signal_ts",
                            name="uq_signal_per_bar"),
    )

    # 6. paper_orders (signal_id is NULL for end-of-day forced exits)
    op.create_table(
        "paper_orders",
        pk(),
        sa.Column("strategy_run_id", sa.BigInteger,
                  sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_id", sa.BigInteger,
                  sa.ForeignKey("signals.id", ondelete="CASCADE"), nullable=True),
        sa.Column("origin", sa.Text, nullable=False),            # 'signal' | 'eod_exit'
        sa.Column("instrument_id", sa.BigInteger,
                  sa.ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("qty", MONEY, nullable=False),
        sa.Column("intended_price", MONEY),
        sa.Column("execution_ts", TS, nullable=False),           # open of the NEXT bar
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("rejection_reason", sa.Text),
        sa.CheckConstraint("origin IN ('signal','eod_exit')", name="ck_order_origin"),
        sa.CheckConstraint("(origin = 'signal') = (signal_id IS NOT NULL)",
                           name="ck_order_origin_signal"),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_order_side"),
        sa.CheckConstraint("qty > 0", name="ck_order_qty_positive"),
        sa.CheckConstraint(
            "status IN ('filled','rejected','cancelled')", name="ck_order_status"),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_order_rejection_reason"),
    )
    op.create_index("ix_paper_orders_run", "paper_orders", ["strategy_run_id", "execution_ts"])

    # 7. paper_fills  (cash ledger audit trail)
    op.create_table(
        "paper_fills",
        pk(),
        sa.Column("order_id", sa.BigInteger,
                  sa.ForeignKey("paper_orders.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("fill_ts", TS, nullable=False),
        sa.Column("fill_price", MONEY, nullable=False),
        sa.Column("qty", MONEY, nullable=False),
        sa.Column("commission", MONEY, nullable=False),
        sa.Column("spread_cost", MONEY, nullable=False),
        sa.Column("slippage_cost", MONEY, nullable=False),
        sa.Column("cash_balance_after", MONEY, nullable=False),
        sa.CheckConstraint("cash_balance_after >= 0", name="ck_no_negative_cash"),
        sa.CheckConstraint("qty > 0", name="ck_fill_qty_positive"),
    )

    # 8. ai_reports (Stage G; table created now, unused until then)
    op.create_table(
        "ai_reports",
        pk(),
        sa.Column("strategy_run_id", sa.BigInteger,
                  sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_text", sa.Text, nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=False),   # exact inputs to the text
        sa.Column("model", sa.Text),
        sa.Column("generated_at", TS, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for t in ("ai_reports", "paper_fills", "paper_orders", "signals",
              "strategy_runs", "price_bars", "compliance_checks", "instruments"):
        op.drop_table(t)
