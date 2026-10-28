"""Turns stored files into chunks and updates the source's status."""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import SourceStatus
from app.ingestion.chunking import chunk_pages
from app.ingestion.pdf import extract_pages
from app.ingestion.transcript import Segment, group_segments
from app.models import Chunk, Source
from app.providers import TranscriptionError, TranscriptionProvider


def ingest_pdf(db: Session, source: Source, pdf_path: Path, settings: Settings) -> None:
    """Extract and chunk a PDF. Raises PDFExtractionError for unreadable files."""
    pages = extract_pages(pdf_path)
    chunks = chunk_pages(pages, settings.pdf_chunk_words, settings.pdf_chunk_overlap)
    db.add_all(
        Chunk(
            tenant_id=source.tenant_id,
            source_id=source.id,
            chunk_index=c.index,
            text=c.text,
            word_count=c.word_count,
            page_number=c.page_start,
            page_end=c.page_end,
        )
        for c in chunks
    )
    source.meta = {
        **source.meta,
        "page_count": len(pages),
        "word_count": sum(len(p.split()) for p in pages),
        "chunk_count": len(chunks),
    }
    if chunks:
        source.status = SourceStatus.READY
        source.error = None
    else:
        source.status = SourceStatus.NEEDS_OCR
        source.error = "No extractable text (scanned PDF?). OCR is not supported yet."


def store_transcript_chunks(
    db: Session,
    source: Source,
    segments: Sequence[Segment],
    settings: Settings,
    origin: str,
) -> None:
    """Replace a video source's chunks with ~N-second groups of transcript segments."""
    chunks = group_segments(segments, settings.transcript_chunk_seconds)
    db.execute(delete(Chunk).where(Chunk.source_id == source.id))
    db.add_all(
        Chunk(
            tenant_id=source.tenant_id,
            source_id=source.id,
            chunk_index=c.index,
            text=c.text,
            word_count=c.word_count,
            start_sec=c.start_sec,
            end_sec=c.end_sec,
        )
        for c in chunks
    )
    source.meta = {
        **source.meta,
        "transcript_origin": origin,
        "segment_count": len(segments),
        "duration_sec": max((s.end for s in segments), default=0.0),
        "chunk_count": len(chunks),
    }
    if chunks:
        source.status = SourceStatus.READY
        source.error = None
    else:
        source.status = SourceStatus.NEEDS_TRANSCRIPT
        source.error = "Transcript contained no text"


def transcribe_source(
    db: Session,
    source: Source,
    media_path: Path,
    transcriber: TranscriptionProvider,
    settings: Settings,
) -> None:
    try:
        segments = transcriber.transcribe(media_path)
    except TranscriptionError as exc:
        source.status = SourceStatus.FAILED
        source.error = str(exc)
        return
    store_transcript_chunks(db, source, segments, settings, origin=transcriber.name)
