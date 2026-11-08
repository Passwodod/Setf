import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import ApprovalStatus, Base, IdMixin, TimestampMixin, pg_enum
from app.models.knowledge import _fk, _owner


class Conversation(Base, IdMixin, TimestampMixin):
    __tablename__ = "conversations"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")

    __table_args__ = (
        CheckConstraint("status IN ('open','closed')", name="status_valid"),
        Index("ix_conversations_owner_person", "owner_id", "person_id"),
    )


class ConversationMessage(Base, IdMixin):
    __tablename__ = "conversation_messages"

    owner_id: Mapped[uuid.UUID] = _owner()
    conversation_id: Mapped[uuid.UUID] = _fk("conversations")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = _fk("approvals", "SET NULL", True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')", name="direction_valid"),
        Index("ix_conversation_messages_conv_created", "conversation_id", "created_at"),
    )


class Approval(Base, IdMixin, TimestampMixin):
    """Outbound message awaiting administrator confirmation."""

    __tablename__ = "approvals"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    conversation_id: Mapped[uuid.UUID | None] = _fk("conversations", "SET NULL", True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(pg_enum(ApprovalStatus, "approval_status"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    admin_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    send_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_approvals_owner_status", "owner_id", "status"),
        CheckConstraint("char_length(text) BETWEEN 1 AND 4096", name="text_length"),
    )


class Interview(Base, IdMixin, TimestampMixin):
    __tablename__ = "interviews"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="invited")
    consent_id: Mapped[uuid.UUID | None] = _fk("consent_records", "SET NULL", True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('invited','active','completed','declined','cancelled')", name="status_valid"),
        Index("ix_interviews_person", "person_id"),
    )


class InterviewAnswer(Base, IdMixin):
    __tablename__ = "interview_answers"

    owner_id: Mapped[uuid.UUID] = _owner()
    interview_id: Mapped[uuid.UUID] = _fk("interviews")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_interview_answers_position", "interview_id", "position", unique=True),
    )
