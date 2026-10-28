import hashlib
import secrets

API_KEY_PREFIX = "aisu_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    # Keys are high-entropy random tokens, so a fast hash is sufficient and
    # allows lookup by hash. Only the hash is stored.
    return hashlib.sha256(api_key.encode()).hexdigest()


def key_prefix(api_key: str) -> str:
    """Short, non-secret identifier to help admins tell keys apart."""
    return api_key[:12]
