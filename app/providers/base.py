"""Vendor-neutral provider interfaces.

Application code depends only on these classes; concrete vendors are wired in
through the registry in `app.providers`.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.ingestion.transcript import Segment


class ProviderError(Exception):
    pass


class TranscriptionError(ProviderError):
    pass


class TranscriptionProvider(ABC):
    """Turns an audio/video file into timed transcript segments."""

    name: str

    @abstractmethod
    def transcribe(self, media_path: Path) -> list[Segment]: ...


class ChatProvider(ABC):
    """Text generation interface for later stages (tutor, summaries)."""

    name: str

    @abstractmethod
    def complete(self, messages: list[dict[str, str]], **options) -> str: ...
