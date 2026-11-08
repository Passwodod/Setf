import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


def FK_OWNER():  # noqa: N802
    return ForeignKey("users.id", ondelete="CASCADE")


class Person(Base, IdMixin, TimestampMixin):
    __tablename__ = "persons"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), FK_OWNER(), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    birth_year: Mapped[int | None] = mapped_column(Integer)  # only if voluntarily disclosed
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, server_default="{}")
    country: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(120))
    education: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    do_not_profile: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    bot_chat_id: Mapped[int | None] = mapped_column(BigInteger)  # set only after the person opened the bot
    search_tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(display_name,'') || ' ' || coalesce(notes,''))", persisted=True),
    )

    __table_args__ = (
        CheckConstraint("birth_year IS NULL OR (birth_year BETWEEN 1900 AND 2100)", name="birth_year_range"),
        Index("ix_persons_owner_name", "owner_id", "display_name"),
        Index("ix_persons_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class PersonIdentifier(Base, IdMixin, TimestampMixin):
    __tablename__ = "person_identifiers"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), FK_OWNER(), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('telegram_id','telegram_username','other')", name="kind_valid"),
        UniqueConstraint("owner_id", "kind", "value", name="uq_person_identifiers_owner_kind_value"),
        Index("ix_person_identifiers_person", "person_id"),
    )


class Group(Base, IdMixin, TimestampMixin):
    __tablename__ = "groups"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), FK_OWNER(), nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="pending")
    collection_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    bot_can_read_all: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        CheckConstraint("status IN ('pending','active','paused','disconnected')", name="status_valid"),
        CheckConstraint("retention_days IS NULL OR retention_days > 0", name="retention_positive"),
        UniqueConstraint("owner_id", "telegram_chat_id", name="uq_groups_owner_chat"),
    )


class GroupMember(Base, IdMixin, TimestampMixin):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("group_id", "person_id", name="uq_group_members_group_person"),
        Index("ix_group_members_person", "person_id"),
    )


class Message(Base, IdMixin):
    __tablename__ = "messages"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), FK_OWNER(), nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    author_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("persons.id", ondelete="SET NULL"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    text_tsv = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))

    __table_args__ = (
        UniqueConstraint("group_id", "telegram_message_id", name="uq_messages_group_tgmsg"),
        Index("ix_messages_group_sent", "group_id", "sent_at"),
        Index("ix_messages_author_sent", "author_person_id", "sent_at"),
        Index("ix_messages_owner_unprocessed", "owner_id", postgresql_where="processed_at IS NULL"),
        Index("ix_messages_text_tsv", "text_tsv", postgresql_using="gin"),
    )


class MessageSource(Base, IdMixin):
    """Where a stored message came from and under which consent it was stored."""

    __tablename__ = "message_sources"

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("consent_records.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_update_id: Mapped[int | None] = mapped_column(BigInteger)
    retained_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
