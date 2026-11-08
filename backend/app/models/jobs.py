import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class BackgroundJob(Base, IdMixin, TimestampMixin):
    """Durable record of a queued unit of work. Redis decides *when* it runs; this row is the truth
    about *what happened* (status, attempts, error, result)."""

    __tablename__ = "background_jobs"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','retrying','succeeded','dead','cancelled')", name="status_valid"),
        CheckConstraint("max_attempts BETWEEN 1 AND 20", name="attempts_range"),
        UniqueConstraint("owner_id", "kind", "idempotency_key", name="uq_background_jobs_idem"),
        Index("ix_background_jobs_owner_status", "owner_id", "status"),
        Index("ix_background_jobs_status_run", "status", "run_at"),
    )
