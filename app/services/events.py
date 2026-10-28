import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.enums import EventType
from app.models import Learner, LearningEvent, Tenant
from app.schemas import EventBatchIn, EventBatchResult


def _resolve_learners(db: Session, tenant: Tenant, batch: EventBatchIn) -> list[Learner]:
    """Return the learner for each event, in order.

    Events may reference a learner by internal `learner_id` or by the host LMS
    `external_user_id`; unknown external ids are provisioned on the fly so an
    LMS can stream events without a separate sync step.
    """
    internal_ids = {e.learner_id for e in batch.events if e.learner_id}
    external_ids = {e.external_user_id for e in batch.events if e.external_user_id}

    by_id: dict[str, Learner] = {}
    if internal_ids:
        by_id = {
            learner.id: learner
            for learner in db.scalars(
                select(Learner).where(Learner.tenant_id == tenant.id, Learner.id.in_(internal_ids))
            )
        }
    by_external: dict[str, Learner] = {}
    if external_ids:
        by_external = {
            learner.external_user_id: learner
            for learner in db.scalars(
                select(Learner).where(
                    Learner.tenant_id == tenant.id, Learner.external_user_id.in_(external_ids)
                )
            )
        }

    errors: list[dict] = []
    resolved: list[Learner] = []
    for index, event in enumerate(batch.events):
        if event.learner_id:
            learner = by_id.get(event.learner_id)
            if learner is None:
                errors.append({"index": index, "error": f"Unknown learner_id {event.learner_id!r}"})
                continue
            if event.external_user_id and event.external_user_id != learner.external_user_id:
                errors.append({"index": index, "error": "learner_id and external_user_id disagree"})
                continue
        else:
            learner = by_external.get(event.external_user_id)
            if learner is None:
                learner = Learner(tenant_id=tenant.id, external_user_id=event.external_user_id)
                db.add(learner)
                by_external[event.external_user_id] = learner
        resolved.append(learner)

    if errors:
        raise HTTPException(422, {"message": "Invalid events", "errors": errors})
    db.flush()  # assigns ids to newly provisioned learners
    return resolved


def ingest_events(db: Session, tenant: Tenant, batch: EventBatchIn) -> EventBatchResult:
    """Store a batch atomically. Events whose statement_id already exists are skipped."""
    learners = _resolve_learners(db, tenant, batch)
    statement_ids = [str(e.statement_id or uuid.uuid4()) for e in batch.events]

    seen = set(
        db.scalars(
            select(LearningEvent.statement_id).where(
                LearningEvent.tenant_id == tenant.id,
                LearningEvent.statement_id.in_(statement_ids),
            )
        )
    )
    accepted = 0
    for event, learner, statement_id in zip(batch.events, learners, statement_ids):
        if statement_id in seen:
            continue
        seen.add(statement_id)
        db.add(
            LearningEvent(
                tenant_id=tenant.id,
                learner_id=learner.id,
                statement_id=statement_id,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                session_id=event.session_id,
                lesson_id=event.lesson_id,
                topic_id=event.topic_id,
                content_format=event.content_format,
                duration_sec=event.duration_sec,
                expected_duration_sec=event.expected_duration_sec,
                score=event.score,
                properties=event.properties,
            )
        )
        accepted += 1
    db.commit()
    return EventBatchResult(
        accepted=accepted,
        duplicates=len(statement_ids) - accepted,
        statement_ids=statement_ids,
    )


def event_query(
    tenant: Tenant,
    learner_id: str | None = None,
    event_type: EventType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Select:
    query = select(LearningEvent).where(LearningEvent.tenant_id == tenant.id)
    if learner_id:
        query = query.where(LearningEvent.learner_id == learner_id)
    if event_type:
        query = query.where(LearningEvent.event_type == event_type)
    if since:
        query = query.where(LearningEvent.occurred_at >= since)
    if until:
        query = query.where(LearningEvent.occurred_at < until)
    return query
