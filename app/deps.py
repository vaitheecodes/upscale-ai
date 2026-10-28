import hmac
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Tenant
from app.providers import TranscriptionProvider
from app.security import hash_api_key

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="TenantApiKey")
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False, scheme_name="AdminKey")


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_transcriber(request: Request) -> TranscriptionProvider | None:
    return request.app.state.transcriber


def get_tenant(
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> Tenant:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Missing or invalid X-API-Key",
        headers={"WWW-Authenticate": "ApiKey"},
    )
    if not api_key:
        raise unauthorized
    tenant = db.scalar(select(Tenant).where(Tenant.api_key_hash == hash_api_key(api_key)))
    if tenant is None or not tenant.is_active:
        raise unauthorized
    return tenant


def require_admin(
    admin_key: str | None = Security(admin_key_header),
    settings: Settings = Depends(get_app_settings),
) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Admin API disabled: set ADMIN_API_KEY")
    if not admin_key or not hmac.compare_digest(admin_key, settings.admin_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-Admin-Key")
