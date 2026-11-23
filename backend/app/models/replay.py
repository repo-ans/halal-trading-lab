import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, UTCDateTime, pk_column

# Kept in sync with migration 0008's CHECK constraints and
# app/services/replay_engine.py's OrderRejected reasons -- same spirit as
# app/models/paper.py::REJECTION_REASONS, but a rejection is never persisted
# here: a rejected manual click returns an error to the caller immediately,
# so only a fill that actually happened is ever written to replay_fills.
REPLAY_FILL_ORIGINS = ("manual", "eod_exit")


class ReplaySession(Base):
    """One user, one instrument, one historical trading day -- Replay Trading
    (CLAUDE.md section 1's day-trading model, applied interactively: a human
    clicking Buy/Sell against a replaying chart, instead of the 5/20 MA
    crossover). Deliberately separate from strategy_runs: that table is the
    automated-backtest/determinism machinery (config_hash, many instruments at
    once, replay via snapshot) -- this is a single manual session with no
    determinism requirement, since the human's own clicks are the "signal"
    here, not a reproducible algorithm. Long-only, one open position at a
    time, max $200/position -- the exact same risk rules as Stage E, just
    reused rather than re-specified (CLAUDE.md section 1: no shorting, ever --
    there is no "Reverse" action, only Buy/Sell)."""

    __tablename__ = "replay_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active','completed')", name="ck_replay_session_status"),
        Index("ix_replay_sessions_user", "user_id", "started_at"),
    )

    id: Mapped[int] = pk_column()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # Supabase auth.users id
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"))
    session_day: Mapped[date] = mapped_column(Date)  # the historical trading day being replayed
    starting_cash: Mapped[Decimal] = mapped_column(Money, server_default="1000")
    max_position_value: Mapped[Decimal] = mapped_column(Money, server_default="200")
    # Assumptions, not researched figures -- same caveat as strategy_runs' columns
    # of the same name (CLAUDE.md section 11, open owner decision).
    commission_per_order: Mapped[Decimal] = mapped_column(Money, server_default="1.00")
    spread_bps: Mapped[Decimal] = mapped_column(Money, server_default="2")
    slippage_bps: Mapped[Decimal] = mapped_column(Money, server_default="1")
    # Snapshot, exactly like strategy_runs.compliance_check_ids (CLAUDE.md
    # section 7's Stage D/E determinism note) -- the gate is evaluated ONCE at
    # session creation with the real clock, never re-checked mid-session even
    # if the record expires while the user is still clicking through the same
    # historical day.
    compliance_check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compliance_checks.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, server_default="active")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ReplayFill(Base):
    """The ledger and trade history, in one table -- Replay Trading has no
    separate "orders" table (unlike paper_orders/paper_fills): a rejected
    manual click is an immediate API error, never written anywhere, so only a
    fill that actually happened is ever persisted here. The current position
    is DERIVED from this table (the latest row for a session), never stored
    redundantly -- same principle as paper_fills."""

    __tablename__ = "replay_fills"
    __table_args__ = (
        CheckConstraint("origin IN ('manual','eod_exit')", name="ck_replay_fill_origin"),
        CheckConstraint("side IN ('buy','sell')", name="ck_replay_fill_side"),
        CheckConstraint("qty > 0", name="ck_replay_fill_qty_positive"),
        CheckConstraint("cash_balance_after >= 0", name="ck_replay_fill_cash_nonneg"),
        Index("ix_replay_fills_session", "replay_session_id", "id"),
    )

    id: Mapped[int] = pk_column()
    replay_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("replay_sessions.id", ondelete="CASCADE"))
    origin: Mapped[str] = mapped_column(Text)  # 'manual' | 'eod_exit'
    side: Mapped[str] = mapped_column(Text)  # 'buy' | 'sell'
    fill_ts: Mapped[datetime] = mapped_column(UTCDateTime)  # the execution bar's open time
    fill_price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Money)
    commission: Mapped[Decimal] = mapped_column(Money)
    spread_cost: Mapped[Decimal] = mapped_column(Money)
    slippage_cost: Mapped[Decimal] = mapped_column(Money)
    cash_balance_after: Mapped[Decimal] = mapped_column(Money)
    # Only set on a 'sell' fill -- nothing is realized until a position closes.
    # CLAUDE.md section 7 / Stage F's formula, applied per-fill instead of after
    # the fact: (sell.fill_price - buy.fill_price) * qty - buy.commission -
    # sell.commission. Lets the UI show a trade's P&L the instant it closes,
    # without recomputing from the whole fill history.
    realized_pnl: Mapped[Decimal | None] = mapped_column(Money)
