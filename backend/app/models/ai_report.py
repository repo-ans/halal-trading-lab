from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, pk_column


class AiReport(Base):
    """Stage G. Created now, unused until then. Numbers in `metrics` must be serialised
    from Decimal as strings, not floats."""

    __tablename__ = "ai_reports"

    id: Mapped[int] = pk_column()
    strategy_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    report_text: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
