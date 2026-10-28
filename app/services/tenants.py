from sqlalchemy.orm import Session

from app.models import Tenant
from app.security import generate_api_key, hash_api_key, key_prefix


def create_tenant(
    db: Session,
    name: str,
    lms_type: str | None = None,
    lms_base_url: str | None = None,
) -> tuple[Tenant, str]:
    """Create a tenant and return it with its plaintext API key (shown only once)."""
    api_key = generate_api_key()
    tenant = Tenant(
        name=name,
        lms_type=lms_type,
        lms_base_url=lms_base_url.rstrip("/") if lms_base_url else None,
        api_key_hash=hash_api_key(api_key),
        api_key_prefix=key_prefix(api_key),
    )
    db.add(tenant)
    db.flush()
    return tenant, api_key


def rotate_api_key(tenant: Tenant) -> str:
    api_key = generate_api_key()
    tenant.api_key_hash = hash_api_key(api_key)
    tenant.api_key_prefix = key_prefix(api_key)
    return api_key
