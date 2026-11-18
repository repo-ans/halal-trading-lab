from datetime import datetime

from sqlalchemy import CHAR, Boolean, CheckConstraint, Text, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, pk_column


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("symbol", "exchange", name="uq_instruments_symbol_exchange"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_instruments_currency_fmt"),
    )

    id: Mapped[int] = pk_column()
    symbol: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str] = mapped_column(Text)  # MIC code
    currency: Mapped[str] = mapped_column(CHAR(3))
    name: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
