from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, pk_column


class AiReport(Base):
    """Stage G. `metrics` (the stored input snapshot) must be serialised from
    Decimal as strings, not floats -- same rule as everywhere else.

    One row per (strategy_run_id, requested_mode) -- not per (strategy_run_id,
    mode). Keyed by what was ASKED for, not what was actually rendered, so a
    request for 'llm' that fell back to the template can still be retried later
    (while llm_attempts < 3 and the daily cap isn't hit) without colliding with
    a separate, independently-requested 'template' row for the same run."""

    __tablename__ = "ai_reports"
    __table_args__ = (
        CheckConstraint("mode IN ('template','llm')", name="ck_ai_report_mode"),
        CheckConstraint("requested_mode IN ('template','llm')", name="ck_ai_report_requested_mode"),
        CheckConstraint("llm_attempts >= 0", name="ck_ai_report_llm_attempts_nonneg"),
        UniqueConstraint("strategy_run_id", "requested_mode", name="uq_ai_report_run_requested_mode"),
    )

    id: Mapped[int] = pk_column()
    strategy_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    report_text: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    # Stage G, migration 0006.
    mode: Mapped[str] = mapped_column(Text)  # what was actually rendered -- drives the heading
    requested_mode: Mapped[str] = mapped_column(Text)  # what the caller asked for -- the lookup/retry key
    llm_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    model_version: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
