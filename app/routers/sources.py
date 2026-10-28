import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_app_settings, get_db, get_tenant, get_transcriber
from app.enums import SourceKind, SourceStatus
from app.ingestion.pdf import PDFExtractionError
from app.ingestion.transcript import TranscriptParseError, parse_transcript
from app.models import Chunk, Source, Tenant, new_id
from app.providers import TranscriptionProvider
from app.schemas import ChunkOut, Page, SourceOut
from app.services.ingestion import ingest_pdf, store_transcript_chunks, transcribe_source
from app.storage import read_upload, safe_filename, save_upload, source_dir

router = APIRouter(prefix="/sources", tags=["sources"])

PDF_EXTENSIONS = {".pdf"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpeg", ".mp3", ".m4a", ".wav"}
TRANSCRIPT_EXTENSIONS = {".srt", ".vtt"}
MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024


def _check_extension(upload: UploadFile, allowed: set[str], label: str) -> str:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{label} must be one of: {', '.join(sorted(allowed))}",
        )
    return ext


def _get_source(db: Session, tenant: Tenant, source_id: str) -> Source:
    source = db.scalar(select(Source).where(Source.id == source_id, Source.tenant_id == tenant.id))
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return source


def _new_source(db: Session, tenant: Tenant, kind: SourceKind, upload: UploadFile, title: str | None) -> Source:
    source = Source(
        id=new_id(),
        tenant_id=tenant.id,
        kind=kind,
        title=(title or "").strip() or Path(upload.filename or "").stem or kind.value,
        original_filename=upload.filename,
        status=SourceStatus.PROCESSING,
        meta={},
    )
    db.add(source)
    db.flush()  # insert before its chunks so the FK is satisfied
    return source


@contextmanager
def _discard_on_error(folder: Path) -> Iterator[None]:
    """Remove a new source's files if the request fails; the DB rolls back on its own."""
    try:
        yield
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _apply_uploaded_transcript(
    db: Session, source: Source, upload: UploadFile, folder: Path, settings: Settings
) -> None:
    ext = _check_extension(upload, TRANSCRIPT_EXTENSIONS, "Transcript")
    raw = read_upload(upload, MAX_TRANSCRIPT_BYTES)
    try:
        segments = parse_transcript(raw)
    except TranscriptParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Could not parse transcript: {exc}") from exc
    transcript_path = folder / f"transcript{ext}"
    transcript_path.write_bytes(raw)
    store_transcript_chunks(db, source, segments, settings, origin="uploaded")
    source.meta = {**source.meta, "transcript_file": transcript_path.name}


@router.post("/pdf", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def upload_pdf(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    _check_extension(file, PDF_EXTENSIONS, "PDF upload")
    source = _new_source(db, tenant, SourceKind.PDF, file, title)
    folder = source_dir(settings, tenant.id, source.id)
    with _discard_on_error(folder):
        path = save_upload(file, folder / safe_filename(file.filename, "document.pdf"), settings.max_upload_bytes)
        source.file_path = str(path)
        try:
            ingest_pdf(db, source, path, settings)
        except PDFExtractionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        db.commit()
    return source


@router.post("/video", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def upload_video(
    file: UploadFile = File(..., description="Video or audio file"),
    transcript: UploadFile | None = File(None, description="Optional .srt or .vtt transcript"),
    title: str | None = Form(None),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    transcriber: TranscriptionProvider | None = Depends(get_transcriber),
):
    """Store a video and chunk its transcript into ~60 s windows.

    Transcript precedence: uploaded .srt/.vtt, then the configured
    transcription provider, otherwise the source is marked `needs_transcript`
    and a transcript can be attached later.
    """
    _check_extension(file, MEDIA_EXTENSIONS, "Video upload")
    has_transcript = transcript is not None and bool(transcript.filename)
    if has_transcript:
        _check_extension(transcript, TRANSCRIPT_EXTENSIONS, "Transcript")

    source = _new_source(db, tenant, SourceKind.VIDEO, file, title)
    folder = source_dir(settings, tenant.id, source.id)
    with _discard_on_error(folder):
        video_path = save_upload(file, folder / safe_filename(file.filename, "video"), settings.max_upload_bytes)
        source.file_path = str(video_path)
        if has_transcript:
            _apply_uploaded_transcript(db, source, transcript, folder, settings)
        elif transcriber is not None:
            transcribe_source(db, source, video_path, transcriber, settings)
        else:
            source.status = SourceStatus.NEEDS_TRANSCRIPT
            source.error = "No transcript uploaded and no transcription provider configured"
        db.commit()
    return source


@router.post("/{source_id}/transcript", response_model=SourceOut)
def attach_transcript(
    source_id: str,
    transcript: UploadFile = File(..., description=".srt or .vtt transcript"),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    """Attach or replace the transcript of a video source."""
    source = _get_source(db, tenant, source_id)
    if source.kind != SourceKind.VIDEO:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transcripts can only be attached to video sources")
    _apply_uploaded_transcript(db, source, transcript, source_dir(settings, tenant.id, source.id), settings)
    db.commit()
    return source


@router.get("", response_model=Page[SourceOut])
def list_sources(
    kind: SourceKind | None = None,
    source_status: SourceStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    query = select(Source).where(Source.tenant_id == tenant.id)
    if kind:
        query = query.where(Source.kind == kind)
    if source_status:
        query = query.where(Source.status == source_status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Source.created_at.desc(), Source.id).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: str, tenant: Tenant = Depends(get_tenant), db: Session = Depends(get_db)):
    return _get_source(db, tenant, source_id)


@router.get("/{source_id}/chunks", response_model=Page[ChunkOut])
def list_chunks(
    source_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    source = _get_source(db, tenant, source_id)
    query = select(Chunk).where(Chunk.tenant_id == tenant.id, Chunk.source_id == source.id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Chunk.chunk_index).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)
