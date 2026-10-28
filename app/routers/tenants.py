from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, get_tenant, require_admin
from app.models import Tenant
from app.schemas import TenantIn, TenantOut, TenantWithKey
from app.services.tenants import create_tenant, rotate_api_key

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
router = APIRouter(tags=["tenant"])


@admin_router.post("/tenants", response_model=TenantWithKey, status_code=status.HTTP_201_CREATED)
def create(payload: TenantIn, db: Session = Depends(get_db)):
    if db.scalar(select(Tenant.id).where(Tenant.name == payload.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A tenant with this name already exists")
    tenant, api_key = create_tenant(db, payload.name, payload.lms_type, payload.lms_base_url)
    db.commit()
    return TenantWithKey(**TenantOut.model_validate(tenant).model_dump(), api_key=api_key)


@admin_router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).order_by(Tenant.created_at)).all()


@admin_router.post("/tenants/{tenant_id}/rotate-key", response_model=TenantWithKey)
def rotate_key(tenant_id: str, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    api_key = rotate_api_key(tenant)
    db.commit()
    return TenantWithKey(**TenantOut.model_validate(tenant).model_dump(), api_key=api_key)


@router.get("/tenant", response_model=TenantOut, summary="Current tenant (connection check)")
def current_tenant(tenant: Tenant = Depends(get_tenant)):
    return tenant
