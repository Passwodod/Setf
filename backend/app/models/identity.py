import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base, ConsentScope, ConsentStatus, IdMixin, Role, TimestampMixin, pg_enum,
)


class User(Base, IdMixin, TimestampMixin):
    """Platform account (the administrator/owner of a private workspace)."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    role: Mapped[Role] = mapped_column(pg_enum(Role, "role"), nullable=False, server_default="owner")
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(email), unique=True),
        Index("uq_users_telegram_user_id", "telegram_user_id", unique=True, postgresql_where="telegram_user_id IS NOT NULL"),
    )


class ConsentRecord(Base, IdMixin, TimestampMixin):
    """Append-only consent ledger. Current state = latest row per (subject, scope)."""

    __tablename__ = "consent_records"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"))
    scope: Mapped[ConsentScope] = mapped_column(pg_enum(ConsentScope, "consent_scope"), nullable=False)
    status: Mapped[ConsentStatus] = mapped_column(pg_enum(ConsentStatus, "consent_status"), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. bot_button, admin_attestation
    notice_text: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("person_id IS NOT NULL OR group_id IS NOT NULL", name="subject_present"),
        Index("ix_consent_records_owner_subject", "owner_id", "person_id", "group_id", "scope", "recorded_at"),
    )


class DataRequest(Base, IdMixin, TimestampMixin):
    """Export / deletion requests with an auditable outcome."""

    __tablename__ = "data_requests"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('export','delete_person','delete_group','delete_messages','retention')", name="kind_valid"),
        CheckConstraint("status IN ('pending','running','completed','failed')", name="status_valid"),
    )


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # user:<id>, bot, system, worker
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_audit_logs_owner_created", "owner_id", "created_at"),)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)  # dynamic structure by design
