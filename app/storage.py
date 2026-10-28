"""Local file storage for uploads: <UPLOAD_DIR>/<tenant_id>/<source_id>/<file>."""

import re
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import Settings

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_READ_SIZE = 1024 * 1024


def safe_filename(name: str | None, default: str) -> str:
    cleaned = _UNSAFE.sub("_", Path(name or "").name).strip("._")
    return cleaned[:200] or default


def source_dir(settings: Settings, tenant_id: str, source_id: str) -> Path:
    path = Path(settings.upload_dir) / tenant_id / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _too_large(max_bytes: int) -> HTTPException:
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        f"File exceeds the {max_bytes / 1024 / 1024:g} MB limit",
    )


def save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> Path:
    """Stream an upload to disk, enforcing a size limit."""
    written = 0
    with dest.open("wb") as out:
        while block := upload.file.read(_READ_SIZE):
            written += len(block)
            if written > max_bytes:
                break
            out.write(block)
    if written > max_bytes:
        dest.unlink(missing_ok=True)
        raise _too_large(max_bytes)
    return dest


def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read a small upload (e.g. a transcript) into memory, enforcing a size limit."""
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    return data
