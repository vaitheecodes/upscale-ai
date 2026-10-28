"""Provider registry. Register additional vendors with `register_transcription_provider`."""

from collections.abc import Callable

from app.config import Settings
from app.providers.base import (
    ChatProvider,
    ProviderError,
    TranscriptionError,
    TranscriptionProvider,
)

TranscriptionFactory = Callable[[Settings], TranscriptionProvider]

_transcription_factories: dict[str, TranscriptionFactory] = {}


def register_transcription_provider(name: str, factory: TranscriptionFactory) -> None:
    _transcription_factories[name.lower()] = factory


def _openai_factory(settings: Settings) -> TranscriptionProvider:
    from app.providers.openai import OpenAITranscriptionProvider

    if not settings.openai_api_key:
        raise ProviderError("TRANSCRIPTION_PROVIDER=openai requires OPENAI_API_KEY")
    return OpenAITranscriptionProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_transcription_model,
        base_url=settings.openai_base_url,
        ffmpeg_path=settings.ffmpeg_path or None,
        audio_bitrate_kbps=settings.transcription_audio_bitrate_kbps,
    )


register_transcription_provider("openai", _openai_factory)


def build_transcription_provider(settings: Settings) -> TranscriptionProvider | None:
    """Resolve the configured provider; None means transcription is unavailable."""
    name = settings.transcription_provider.lower()
    if name == "none":
        return None
    if name == "auto":
        return _openai_factory(settings) if settings.openai_api_key else None
    factory = _transcription_factories.get(name)
    if factory is None:
        raise ProviderError(f"Unknown transcription provider: {settings.transcription_provider}")
    return factory(settings)


__all__ = [
    "ChatProvider",
    "ProviderError",
    "TranscriptionError",
    "TranscriptionProvider",
    "build_transcription_provider",
    "register_transcription_provider",
]
