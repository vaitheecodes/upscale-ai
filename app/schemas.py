import uuid
from datetime import date, datetime, timezone
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import ContentFormat, EventType, SourceKind, SourceStatus

T = TypeVar("T")


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {value!r}") from exc
    return value


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# --- Tenants -----------------------------------------------------------------


class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    lms_type: str | None = Field(None, max_length=50, examples=["moodle"])
    lms_base_url: str | None = Field(
        None, max_length=500, pattern=r"^https?://", examples=["https://moodle.example.edu"]
    )


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    lms_type: str | None
    lms_base_url: str | None
    api_key_prefix: str
    is_active: bool
    created_at: datetime


class TenantWithKey(TenantOut):
    api_key: str = Field(description="Shown once. Send it as the X-API-Key header.")


# --- Sources -----------------------------------------------------------------


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: SourceKind
    title: str
    original_filename: str | None
    status: SourceStatus
    error: str | None
    metadata: dict[str, Any] = Field(validation_alias="meta")
    created_at: datetime
    updated_at: datetime


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    chunk_index: int
    text: str
    word_count: int
    page_number: int | None
    page_end: int | None
    start_sec: float | None
    end_sec: float | None


# --- Learners ----------------------------------------------------------------


class LearnerIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(None, max_length=255)
    timezone: str | None = Field(None, description="IANA name; defaults to Asia/Kolkata")

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return validate_timezone(value) if value is not None else None


class LearnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_user_id: str
    display_name: str | None
    timezone: str
    created_at: datetime


# --- Events ------------------------------------------------------------------


class EventIn(BaseModel):
    """A learning event. Mirrors an xAPI statement:
    actor = learner, verb = event_type, object = lesson/topic, result = score/duration.
    """

    model_config = ConfigDict(extra="forbid")

    statement_id: uuid.UUID | None = Field(
        None, description="xAPI statement id. Generated if omitted; reuse it when retrying."
    )
    learner_id: str | None = Field(None, description="Internal learner id")
    external_user_id: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Host LMS user id; the learner is created on first use",
    )
    event_type: EventType
    occurred_at: datetime
    session_id: str | None = Field(None, max_length=255)
    lesson_id: str | None = Field(None, max_length=255)
    topic_id: str | None = Field(None, max_length=255)
    content_format: ContentFormat | None = None
    duration_sec: float | None = Field(None, ge=0)
    expected_duration_sec: float | None = Field(None, ge=0)
    score: float | None = Field(None, ge=0, le=1, description="Scaled score 0..1 (xAPI score.scaled)")
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def _requires_learner_ref(self) -> "EventIn":
        if not self.learner_id and not self.external_user_id:
            raise ValueError("Provide learner_id or external_user_id")
        return self


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1)


class EventBatchResult(BaseModel):
    accepted: int
    duplicates: int
    statement_ids: list[str] = Field(description="Statement id of each submitted event, in order")


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    statement_id: str
    learner_id: str
    event_type: EventType
    occurred_at: datetime
    received_at: datetime
    session_id: str | None
    lesson_id: str | None
    topic_id: str | None
    content_format: ContentFormat | None
    duration_sec: float | None
    expected_duration_sec: float | None
    score: float | None
    properties: dict[str, Any]


# --- Activity summary --------------------------------------------------------


class HourBucket(BaseModel):
    hour: int = Field(ge=0, le=23, description="Hour of day in the learner's timezone")
    event_count: int


class WeekActivity(BaseModel):
    week_start: date = Field(description="Monday of the ISO week, learner's timezone")
    days_active: int


class FormatStats(BaseModel):
    content_format: ContentFormat
    lessons_opened: int
    lessons_completed: int
    lessons_skipped: int
    completion_rate: float | None
    skip_rate: float | None
    avg_duration_ratio: float | None = Field(
        description="Mean duration_sec / expected_duration_sec over completed lessons"
    )


class ActivitySummary(BaseModel):
    learner_id: str
    timezone: str
    since: datetime | None
    until: datetime | None
    total_events: int
    first_event_at: datetime | None
    last_event_at: datetime | None
    total_active_days: int
    active_hours: list[HourBucket]
    peak_hours: list[int]
    weekly_activity: list[WeekActivity]
    avg_days_active_per_week: float | None
    content_formats: list[FormatStats]
