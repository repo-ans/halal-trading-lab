from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, UTCDateTime, pk_column


class PaperOrder(Base):
    """signal_id is NULL for end-of-day forced exits (origin = 'eod_exit')."""

    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint("origin IN ('signal','eod_exit')", name="ck_order_origin"),
        CheckConstraint("(origin = 'signal') = (signal_id IS NOT NULL)", name="ck_order_origin_signal"),
        CheckConstraint("side IN ('buy','sell')", name="ck_order_side"),
        CheckConstraint("qty > 0", name="ck_order_qty_positive"),
        CheckConstraint("status IN ('filled','rejected','cancelled')", name="ck_order_status"),
        CheckConstraint("(status = 'rejected') = (rejection_reason IS NOT NULL)",
                        name="ck_order_rejection_reason"),
        Index("ix_paper_orders_run", "strategy_run_id", "execution_ts"),
    )

    id: Mapped[int] = pk_column()
    strategy_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("signals.id", ondelete="CASCADE"))
    origin: Mapped[str] = mapped_column(Text)
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"))
    side: Mapped[str] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Money)
    intended_price: Mapped[Decimal | None] = mapped_column(Money)
    execution_ts: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class PaperFill(Base):
    """Cash ledger audit trail. Positions are DERIVED from this table, never stored."""

    __tablename__ = "paper_fills"
    __table_args__ = (
        CheckConstraint("cash_balance_after >= 0", name="ck_no_negative_cash"),
        CheckConstraint("qty > 0", name="ck_fill_qty_positive"),
    )

    id: Mapped[int] = pk_column()
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("paper_orders.id", ondelete="CASCADE"), unique=True)
    fill_ts: Mapped[datetime] = mapped_column(UTCDateTime)
    fill_price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Money)
    commission: Mapped[Decimal] = mapped_column(Money)
    spread_cost: Mapped[Decimal] = mapped_column(Money)
    slippage_cost: Mapped[Decimal] = mapped_column(Money)
    cash_balance_after: Mapped[Decimal] = mapped_column(Money)
