from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, UTCDateTime, pk_column

# The only interval the app stores or serves. Single source of truth -- routes and
# services import this rather than each keeping their own "5m" literal.
SUPPORTED_INTERVAL = "5m"


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("instrument_id", "ts", "interval", name="uq_price_bars_idempotent"),
        CheckConstraint("high >= low", name="ck_bar_high_ge_low"),
        Index("ix_price_bars_backtest", "instrument_id", "interval", "ts"),
    )

    id: Mapped[int] = pk_column()
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)  # bar OPEN time, UTC
    interval: Mapped[str] = mapped_column(Text)
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
