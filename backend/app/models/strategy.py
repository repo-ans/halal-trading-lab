import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (CHAR, BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
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
        CheckConstraint("paper_status IN ('not_run','completed')", name="ck_paper_status"),
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
    # UNCONFIRMED ASSUMPTION 1 (CLAUDE.md section 7): MA continues across sessions by
    # default; this flips that off.
    reset_ma_each_session: Mapped[bool] = mapped_column(Boolean, server_default="false")
    starting_cash: Mapped[Decimal] = mapped_column(Money, server_default="1000")
    max_position_value: Mapped[Decimal] = mapped_column(Money, server_default="200")
    # Defaults are ASSUMPTIONS, not researched figures -- CLAUDE.md section 11 lists
    # them as open CEO decisions. Stage E does not block on confirming them.
    commission_per_order: Mapped[Decimal] = mapped_column(Money, server_default="1.00")
    spread_bps: Mapped[Decimal] = mapped_column(Money, server_default="2")
    slippage_bps: Mapped[Decimal] = mapped_column(Money, server_default="1")
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    # Stage D (migration 0004). Both keyed by instrument_id (JSON object keys are
    # always strings, including when the key started life as a Python int).
    # blocked_instruments: instrument_id -> the gate's reason (never present for a
    # compliant instrument). compliance_check_ids: instrument_id -> the
    # compliance_checks.id the gate used (CLAUDE.md section 7 determinism note --
    # a replay reuses this instead of re-evaluating the gate against the current
    # clock, so it stays reproducible even if the real record expires in between).
    blocked_instruments: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    compliance_check_ids: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # Stage E (migration 0005). The paper engine (app/services/paper_engine.py)
    # locks this row (SELECT ... FOR UPDATE), checks paper_status=='not_run', writes
    # every paper_orders/paper_fills row, and flips this to 'completed' in ONE
    # transaction -- a second call, even for a run that produced zero orders, sees
    # 'completed' and refuses (PaperEngineAlreadyRun) rather than ever duplicating.
    paper_status: Mapped[str] = mapped_column(Text, server_default="not_run")
    paper_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


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
    # Stage D (migration 0004). The next calendar-expected bar open, same trading
    # session as signal_ts -- never the next bar found in the DATA (CLAUDE.md
    # section 7). Next-bar execution is enforced by Stage E reading this column,
    # not by re-deriving it at fill time.
    execution_ts: Mapped[datetime] = mapped_column(UTCDateTime)
