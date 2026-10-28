"""Parsing of SRT / WebVTT transcripts and grouping into time-window chunks."""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TimedChunk:
    index: int
    start_sec: float
    end_sec: float
    text: str
    word_count: int


class TranscriptParseError(ValueError):
    pass


_TIMESTAMP = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING_LINE = re.compile(rf"^\s*({_TIMESTAMP})\s*-->\s*({_TIMESTAMP})")
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n\s*\n")


def parse_timestamp(value: str) -> float:
    """Parse `HH:MM:SS,mmm`, `HH:MM:SS.mmm` or `MM:SS.mmm` into seconds."""
    clock, _, fraction = value.strip().replace(",", ".").partition(".")
    parts = [int(p) for p in clock.split(":")]
    if len(parts) == 2:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    millis = int(fraction.ljust(3, "0")[:3]) if fraction else 0
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _decode(content: str | bytes) -> str:
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = content.decode("latin-1")
    return content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def parse_transcript(content: str | bytes) -> list[Segment]:
    """Parse SRT or WebVTT content into ordered segments.

    Both formats are cue blocks separated by blank lines with a
    `start --> end` timing line, so one parser handles both. Blocks without a
    timing line (WEBVTT header, NOTE, STYLE, REGION) are skipped, as are cue
    identifiers, cue settings and inline markup such as `<v Speaker>`.
    """
    text = _decode(content)
    segments: list[Segment] = []
    for block in _BLANK_LINES.split(text.strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            match = _TIMING_LINE.match(line)
            if match:
                start = parse_timestamp(match.group(1))
                end = max(start, parse_timestamp(match.group(2)))
                cue_text = " ".join(
                    html.unescape(_TAG.sub("", cue_line)).strip() for cue_line in lines[i + 1 :]
                )
                cue_text = " ".join(cue_text.split())
                if cue_text:
                    segments.append(Segment(start=start, end=end, text=cue_text))
                break
    if not segments:
        raise TranscriptParseError("No timed cues found; expected SRT or WebVTT content")
    segments.sort(key=lambda s: s.start)
    return segments


def group_segments(segments: Sequence[Segment], window_sec: float = 60.0) -> list[TimedChunk]:
    """Group consecutive segments into chunks spanning at most ~`window_sec`.

    A new chunk starts when adding a segment would push the chunk past the
    window. A single segment longer than the window becomes its own chunk.
    """
    chunks: list[TimedChunk] = []
    current: list[Segment] = []

    def flush() -> None:
        text = " ".join(s.text for s in current)
        chunks.append(
            TimedChunk(
                index=len(chunks),
                start_sec=current[0].start,
                end_sec=max(s.end for s in current),
                text=text,
                word_count=len(text.split()),
            )
        )

    for segment in segments:
        if current and segment.end - current[0].start > window_sec:
            flush()
            current = []
        current.append(segment)
    if current:
        flush()
    return chunks
