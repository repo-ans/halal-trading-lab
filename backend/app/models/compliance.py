import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Text, false, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, pk_column


class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"
    __table_args__ = (
        CheckConstraint("status IN ('halal','not_halal','doubtful','not_covered')",
                        name="ck_compliance_status"),
        CheckConstraint("source IN ('manual','zoya','musaffa')", name="ck_compliance_source"),
        CheckConstraint("expires_at > checked_at", name="ck_compliance_expiry_after_check"),
    )

    id: Mapped[int] = pk_column()
    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instruments.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    evidence_file_path: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
