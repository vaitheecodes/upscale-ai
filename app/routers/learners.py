from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_db, get_tenant
from app.models import DEFAULT_TIMEZONE, Learner, Tenant
from app.schemas import ActivitySummary, LearnerIn, LearnerOut, Page, as_utc
from app.services.activity import summarize_activity

router = APIRouter(prefix="/learners", tags=["learners"])


def _get_learner(db: Session, tenant: Tenant, learner_id: str) -> Learner:
    learner = db.scalar(select(Learner).where(Learner.id == learner_id, Learner.tenant_id == tenant.id))
    if learner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Learner not found")
    return learner


@router.post(
    "",
    response_model=LearnerOut,
    responses={201: {"description": "Learner created"}, 200: {"description": "Learner updated"}},
)
def upsert_learner(
    payload: LearnerIn,
    response: Response,
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Create a learner, or update the one with the same external_user_id."""
    learner = db.scalar(
        select(Learner).where(
            Learner.tenant_id == tenant.id, Learner.external_user_id == payload.external_user_id
        )
    )
    if learner is None:
        learner = Learner(
            tenant_id=tenant.id,
            external_user_id=payload.external_user_id,
            display_name=payload.display_name,
            timezone=payload.timezone or DEFAULT_TIMEZONE,
        )
        db.add(learner)
        response.status_code = status.HTTP_201_CREATED
    else:
        if payload.display_name is not None:
            learner.display_name = payload.display_name
        if payload.timezone is not None:
            learner.timezone = payload.timezone
    db.commit()
    return learner


@router.get("", response_model=Page[LearnerOut])
def list_learners(
    external_user_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    query = select(Learner).where(Learner.tenant_id == tenant.id)
    if external_user_id:
        query = query.where(Learner.external_user_id == external_user_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Learner.created_at, Learner.id).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{learner_id}", response_model=LearnerOut)
def get_learner(learner_id: str, tenant: Tenant = Depends(get_tenant), db: Session = Depends(get_db)):
    return _get_learner(db, tenant, learner_id)


@router.get("/{learner_id}/activity-summary", response_model=ActivitySummary)
def activity_summary(
    learner_id: str,
    since: datetime | None = Query(None, description="Inclusive lower bound on occurred_at"),
    until: datetime | None = Query(None, description="Exclusive upper bound on occurred_at"),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Active-hours histogram (learner's timezone), days active per week and
    completion / skip rates per content format."""
    learner = _get_learner(db, tenant, learner_id)
    return summarize_activity(
        db,
        learner,
        since=as_utc(since) if since else None,
        until=as_utc(until) if until else None,
    )
