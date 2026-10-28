import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime

DEFAULT_TIMEZONE = "Asia/Kolkata"

JSONType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """A host LMS installation (e.g. one Moodle site) using the plugin API."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    lms_type: Mapped[str | None] = mapped_column(String(50))
    # Used as the xAPI actor account homePage, so it should be the LMS site URL.
    lms_base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    api_key_prefix: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Source(Base):
    """An uploaded learning resource (PDF or video) that is split into chunks."""

    __tablename__ = "sources"
    __table_args__ = (Index("ix_sources_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Chunk(Base):
    """A retrieval unit of a source. PDF chunks carry pages, video chunks carry times."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_chunks_source_index"),
        Index("ix_chunks_tenant_source", "tenant_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Denormalised so tenant-scoped retrieval never needs a join.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    start_sec: Mapped[float | None] = mapped_column(Float)
    end_sec: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Learner(Base):
    __tablename__ = "learners"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_user_id", name="uq_learners_tenant_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    # The user id in the host LMS (e.g. Moodle user id).
    external_user_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class LearningEvent(Base):
    """One learner interaction. Maps to an xAPI statement:
    actor=learner, verb=event_type, object=lesson/topic, result=score/duration.
    """

    __tablename__ = "learning_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "statement_id", name="uq_events_tenant_statement"),
        Index("ix_events_learner_time", "tenant_id", "learner_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"))
    # xAPI statement id; also the idempotency key for retried batches.
    statement_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    session_id: Mapped[str | None] = mapped_column(String(255))
    lesson_id: Mapped[str | None] = mapped_column(String(255))
    topic_id: Mapped[str | None] = mapped_column(String(255))
    content_format: Mapped[str | None] = mapped_column(String(16))
    duration_sec: Mapped[float | None] = mapped_column(Float)
    expected_duration_sec: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
