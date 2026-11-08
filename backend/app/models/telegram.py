import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.knowledge import _fk, _owner


class TelegramAccount(Base, IdMixin, TimestampMixin):
    """A user's own Telegram account connected over MTProto. Read-only: the platform never sends
    through it. The session and api_hash are stored only as Fernet ciphertext."""

    __tablename__ = "telegram_accounts"

    owner_id: Mapped[uuid.UUID] = _owner()
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash_enc: Mapped[str] = mapped_column(Text, nullable=False)
    session_enc: Mapped[str | None] = mapped_column(Text)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="connected")
    last_error: Mapped[str | None] = mapped_column(Text)
    retention_days: Mapped[int | None] = mapped_column(Integer)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('connected','needs_reauth','disconnected')", name="status_valid"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0", name="retention_positive"),
        UniqueConstraint("owner_id", "tg_user_id", name="uq_telegram_accounts_owner_tguser"),
    )


class TelegramSource(Base, IdMixin, TimestampMixin):
    """An explicitly selected source. Nothing is imported unless a row exists and enabled=true."""

    __tablename__ = "telegram_sources"

    owner_id: Mapped[uuid.UUID] = _owner()
    account_id: Mapped[uuid.UUID] = _fk("telegram_accounts")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    person_id: Mapped[uuid.UUID | None] = _fk("persons", "CASCADE", True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups", "SET NULL", True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    retention_days: Mapped[int | None] = mapped_column(Integer)
    backfill_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="200")
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("kind IN ('group','channel','contact','conversation')", name="kind_valid"),
        CheckConstraint("backfill_limit BETWEEN 0 AND 5000", name="backfill_range"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0", name="retention_positive"),
        CheckConstraint("kind NOT IN ('contact','conversation') OR person_id IS NOT NULL", name="private_needs_person"),
        UniqueConstraint("account_id", "peer_id", name="uq_telegram_sources_account_peer"),
    )


class TelegramImportRun(Base, IdMixin):
    __tablename__ = "telegram_import_runs"

    owner_id: Mapped[uuid.UUID] = _owner()
    account_id: Mapped[uuid.UUID] = _fk("telegram_accounts")
    source_id: Mapped[uuid.UUID | None] = _fk("telegram_sources", "SET NULL", True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    stored: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed')", name="status_valid"),
        Index("ix_telegram_import_runs_account_started", "account_id", "started_at"),
    )
