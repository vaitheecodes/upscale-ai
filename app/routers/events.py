from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_app_settings, get_db, get_tenant
from app.enums import EventType
from app.models import Learner, LearningEvent, Tenant
from app.schemas import EventBatchIn, EventBatchResult, EventOut, Page, as_utc
from app.services.events import event_query, ingest_events
from app.services.xapi import to_statement

router = APIRouter(tags=["events"])


class EventFilters:
    def __init__(
        self,
        learner_id: str | None = None,
        event_type: EventType | None = None,
        since: datetime | None = Query(None, description="Inclusive lower bound on occurred_at"),
        until: datetime | None = Query(None, description="Exclusive upper bound on occurred_at"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        self.learner_id = learner_id
        self.event_type = event_type
        self.since = as_utc(since) if since else None
        self.until = as_utc(until) if until else None
        self.limit = limit
        self.offset = offset

    def query(self, tenant: Tenant):
        return event_query(tenant, self.learner_id, self.event_type, self.since, self.until)


@router.post("/events", response_model=EventBatchResult, status_code=status.HTTP_202_ACCEPTED)
def post_events(
    batch: EventBatchIn,
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Ingest a batch of learning events (all-or-nothing).

    Retrying a batch with the same statement_ids is safe: already stored
    events are counted as duplicates instead of being inserted twice.
    """
    if len(batch.events) > settings.max_events_per_batch:
        raise HTTPException(
            422,
            f"Batch too large: max {settings.max_events_per_batch} events",
        )
    return ingest_events(db, tenant, batch)


@router.get("/events", response_model=Page[EventOut])
def list_events(
    filters: EventFilters = Depends(),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    query = filters.query(tenant)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(LearningEvent.occurred_at, LearningEvent.id).limit(filters.limit).offset(filters.offset)
    ).all()
    return Page(items=items, total=total, limit=filters.limit, offset=filters.offset)


@router.get("/xapi/statements", tags=["xapi"], summary="Events as xAPI statements")
def list_xapi_statements(
    request: Request,
    filters: EventFilters = Depends(),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Read-only, LRS-style view of stored events (xAPI 1.0.3 StatementResult)."""
    events = filters.query(tenant).subquery()
    total = db.scalar(select(func.count()).select_from(events))
    rows = db.execute(
        filters.query(tenant)
        .add_columns(Learner)
        .join(Learner, Learner.id == LearningEvent.learner_id)
        .order_by(LearningEvent.occurred_at, LearningEvent.id)
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    next_offset = filters.offset + filters.limit
    more = str(request.url.include_query_params(offset=next_offset)) if next_offset < total else ""
    return {
        "statements": [to_statement(event, learner, tenant, settings.xapi_base_iri) for event, learner in rows],
        "more": more,
    }
