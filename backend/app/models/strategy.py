import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (CHAR, BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, UTCDateTime, pk_column


class StrategyRun(Base):
    __tablename__ = "strategy_runs"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_strategy_runs_currency_fmt"),
        CheckConstraint("fast_period < slow_period", name="ck_ma_periods"),
        CheckConstraint("range_end > range_start", name="ck_run_range"),
        CheckConstraint("status IN ('pending','running','completed','failed')", name="ck_run_status"),
        Index("ix_strategy_runs_user", "user_id", "started_at"),
        Index("ix_strategy_runs_hash", "config_hash"),
    )

    id: Mapped[int] = pk_column()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))  # Supabase auth.users id
    currency: Mapped[str] = mapped_column(CHAR(3))  # single-currency simulation, no FX
    instrument_ids: Mapped[list[int]] = mapped_column(JSONB)
    range_start: Mapped[datetime] = mapped_column(UTCDateTime)
    range_end: Mapped[datetime] = mapped_column(UTCDateTime)
    fast_period: Mapped[int] = mapped_column(Integer, server_default="5")
    slow_period: Mapped[int] = mapped_column(Integer, server_default="20")
    starting_cash: Mapped[Decimal] = mapped_column(Money, server_default="1000")
    max_position_value: Mapped[Decimal] = mapped_column(Money, server_default="200")
    commission_per_order: Mapped[Decimal] = mapped_column(Money)
    spread_bps: Mapped[Decimal] = mapped_column(Money)
    slippage_bps: Mapped[Decimal] = mapped_column(Money)
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="ck_signal_side"),
        UniqueConstraint("strategy_run_id", "instrument_id", "signal_ts", name="uq_signal_per_bar"),
    )

    id: Mapped[int] = pk_column()
    strategy_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"))
    signal_ts: Mapped[datetime] = mapped_column(UTCDateTime)
    side: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    ma_fast: Mapped[Decimal] = mapped_column(Money)
    ma_slow: Mapped[Decimal] = mapped_column(Money)
