import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def pg_enum(enum_cls: type[enum.Enum], name: str):
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Role(str, enum.Enum):
    ADMIN = "admin"
    OWNER = "owner"  # regular platform user who owns their own workspace data
    VIEWER = "viewer"


class VerificationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    SELF_REPORTED = "SELF_REPORTED"
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    CONTRADICTED = "CONTRADICTED"


class Confidence(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SourceType(str, enum.Enum):
    GROUP_MESSAGE = "group_message"
    SELF_REPORT = "self_report"
    ADMIN_NOTE = "admin_note"
    INTERVIEW = "interview"
    BOT_INTERACTION = "bot_interaction"
    MANUAL = "manual"
    AI_INFERENCE = "ai_inference"


class ConsentScope(str, enum.Enum):
    GROUP_COLLECTION = "group_collection"
    PROFILING = "profiling"
    DIRECT_MESSAGING = "direct_messaging"
    INTERVIEW = "interview"


class ConsentStatus(str, enum.Enum):
    GRANTED = "granted"
    REVOKED = "revoked"
    PENDING = "pending"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
