import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base, Confidence, IdMixin, SourceType, TimestampMixin, VerificationStatus, pg_enum,
)
from app.models.people import FK_OWNER


def _fk(table: str, ondelete: str = "CASCADE", nullable: bool = False):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)


def _owner():
    return mapped_column(UUID(as_uuid=True), FK_OWNER(), nullable=False)


class TopicCategory(Base, IdMixin):
    __tablename__ = "topic_categories"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), FK_OWNER())  # NULL = system-wide
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = _fk("topic_categories", "SET NULL", True)

    __table_args__ = (
        Index("uq_topic_categories_owner_slug", func.coalesce(owner_id, uuid.UUID(int=0)), "slug", unique=True),
    )


class Interest(Base, IdMixin, TimestampMixin):
    __tablename__ = "interests"

    owner_id: Mapped[uuid.UUID] = _owner()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[uuid.UUID | None] = _fk("topic_categories", "SET NULL", True)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_interests_owner_name"),)


class PersonInterest(Base, IdMixin, TimestampMixin):
    """stance separates genuine interest from a bare mention or a negative attitude."""

    __tablename__ = "person_interests"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    interest_id: Mapped[uuid.UUID] = _fk("interests")
    stance: Mapped[str] = mapped_column(String(16), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "pi_status"), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(pg_enum(Confidence, "pi_confidence"), nullable=False)
    avoid_topic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    context: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("stance IN ('interest','mention','negative')", name="stance_valid"),
        CheckConstraint("tier IN ('primary','secondary','unknown')", name="tier_valid"),
        UniqueConstraint("person_id", "interest_id", name="uq_person_interests_person_interest"),
        Index("ix_person_interests_owner_interest", "owner_id", "interest_id"),
    )


class ProfessionalInformation(Base, IdMixin, TimestampMixin):
    __tablename__ = "professional_information"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "pro_status"), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(pg_enum(Confidence, "pro_confidence"), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "field IN ('occupation','employer','specialty','skill','industry','goal','technology','financial_interest')",
            name="field_valid",
        ),
        Index("ix_professional_information_person", "person_id", "field"),
    )


class Project(Base, IdMixin, TimestampMixin):
    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="other")
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "proj_status"), nullable=False)

    __table_args__ = (Index("ix_projects_person", "person_id"),)


class Observation(Base, IdMixin, TimestampMixin):
    """One analytic claim with provenance. Rows are never rewritten: changes create new rows linked
    via supersedes_id, and conflicts are kept side by side via contradicts_id."""

    __tablename__ = "observations"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID | None] = _fk("persons", nullable=True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups", nullable=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "obs_source_type"), nullable=False)
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "obs_status"), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(pg_enum(Confidence, "obs_confidence"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = _fk("observations", "SET NULL", True)
    contradicts_id: Mapped[uuid.UUID | None] = _fk("observations", "SET NULL", True)
    model_name: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("person_id IS NOT NULL OR group_id IS NOT NULL", name="subject_present"),
        CheckConstraint("source_type <> 'ai_inference' OR status IN ('INFERRED','UNKNOWN')", name="ai_cannot_assert_fact"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_observations_owner_idem"),
        Index("ix_observations_person_observed", "person_id", "observed_at"),
        Index("ix_observations_group_observed", "group_id", "observed_at"),
    )


class Evidence(Base, IdMixin):
    __tablename__ = "evidence"

    owner_id: Mapped[uuid.UUID] = _owner()
    observation_id: Mapped[uuid.UUID] = _fk("observations")
    message_id: Mapped[uuid.UUID | None] = _fk("messages", "SET NULL", True)
    interview_answer_id: Mapped[uuid.UUID | None] = _fk("interview_answers", "SET NULL", True)
    excerpt: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_evidence_observation", "observation_id"), Index("ix_evidence_message", "message_id"))


class Timeline(Base, IdMixin):
    __tablename__ = "timelines"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID] = _fk("persons")
    observation_id: Mapped[uuid.UUID | None] = _fk("observations", "SET NULL", True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(pg_enum(SourceType, "tl_source_type"), nullable=False)
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "tl_status"), nullable=False)
    message_id: Mapped[uuid.UUID | None] = _fk("messages", "SET NULL", True)

    __table_args__ = (Index("ix_timelines_person_event", "person_id", "event_at"),)


class TopicMention(Base, IdMixin):
    __tablename__ = "topic_mentions"

    owner_id: Mapped[uuid.UUID] = _owner()
    group_id: Mapped[uuid.UUID | None] = _fk("groups", nullable=True)
    person_id: Mapped[uuid.UUID | None] = _fk("persons", "SET NULL", True)
    message_id: Mapped[uuid.UUID | None] = _fk("messages", nullable=True)
    category_id: Mapped[uuid.UUID | None] = _fk("topic_categories", "SET NULL", True)
    interest_id: Mapped[uuid.UUID | None] = _fk("interests", "SET NULL", True)
    topic: Mapped[str] = mapped_column(String(160), nullable=False)
    initiated_topic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    mentioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("message_id", "topic", name="uq_topic_mentions_message_topic"),
        Index("ix_topic_mentions_group_time", "group_id", "mentioned_at"),
        Index("ix_topic_mentions_person_time", "person_id", "mentioned_at"),
    )


class SocialConnection(Base, IdMixin, TimestampMixin):
    """Undirected edge stored with person_a_id < person_b_id. Shared membership is a weak
    'co_membership' kind and must never be presented as friendship."""

    __tablename__ = "social_connections"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_a_id: Mapped[uuid.UUID] = _fk("persons")
    person_b_id: Mapped[uuid.UUID] = _fk("persons")
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "sc_status"), nullable=False)

    __table_args__ = (
        CheckConstraint("person_a_id < person_b_id", name="ordered_pair"),
        CheckConstraint(
            "kind IN ('co_membership','replied_to','shared_interest','shared_project','self_reported')", name="kind_valid"
        ),
        UniqueConstraint("owner_id", "person_a_id", "person_b_id", "kind", name="uq_social_connections_pair_kind"),
    )


class InteractionEvent(Base, IdMixin):
    __tablename__ = "interaction_events"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID | None] = _fk("persons", nullable=True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups", nullable=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_interaction_events_owner_time", "owner_id", "occurred_at"),)


class AiInsight(Base, IdMixin, TimestampMixin):
    __tablename__ = "ai_insights"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID | None] = _fk("persons", nullable=True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups", nullable=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)  # structure varies per insight kind
    status: Mapped[VerificationStatus] = mapped_column(pg_enum(VerificationStatus, "ins_status"), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(pg_enum(Confidence, "ins_confidence"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    superseded_by_id: Mapped[uuid.UUID | None] = _fk("ai_insights", "SET NULL", True)

    __table_args__ = (
        CheckConstraint("status <> 'VERIFIED'", name="ai_never_verified"),
        Index("ix_ai_insights_person_kind", "person_id", "kind"),
    )


class Embedding(Base, IdMixin):
    __tablename__ = "embeddings"

    owner_id: Mapped[uuid.UUID] = _owner()
    person_id: Mapped[uuid.UUID | None] = _fk("persons", nullable=True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups", nullable=True)
    message_id: Mapped[uuid.UUID | None] = _fk("messages", nullable=True)
    observation_id: Mapped[uuid.UUID | None] = _fk("observations", nullable=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vector = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "person_id IS NOT NULL OR group_id IS NOT NULL OR message_id IS NOT NULL OR observation_id IS NOT NULL",
            name="has_subject",
        ),
        UniqueConstraint(
            "owner_id", "model_name", "content_hash", "message_id", "observation_id",
            name="uq_embeddings_content", postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_embeddings_vector", "vector", postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )
