import logging
import shutil
import tempfile
from pathlib import Path

import httpx

from app.ingestion.audio import AudioExtractionError, extract_audio
from app.ingestion.transcript import Segment
from app.providers.base import TranscriptionError, TranscriptionProvider

logger = logging.getLogger(__name__)


class OpenAITranscriptionProvider(TranscriptionProvider):
    """Whisper via the OpenAI REST API (plain HTTP, no vendor SDK).

    With `ffmpeg_path` set, the audio track is first extracted as mono 16 kHz
    low-bitrate MP3, so long videos fit under OpenAI's 25 MB upload limit.
    """

    name = "openai"
    max_file_bytes = 25 * 1024 * 1024  # OpenAI's upload limit for transcription

    def __init__(
        self,
        api_key: str,
        model: str = "whisper-1",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 600.0,
        client: httpx.Client | None = None,
        ffmpeg_path: str | None = None,
        audio_bitrate_kbps: int = 32,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._ffmpeg_path = ffmpeg_path
        self._audio_bitrate_kbps = audio_bitrate_kbps

    def transcribe(self, media_path: Path) -> list[Segment]:
        ffmpeg = shutil.which(self._ffmpeg_path) if self._ffmpeg_path else None
        if ffmpeg is None:
            if self._ffmpeg_path:
                logger.warning("ffmpeg not found at %r; sending the original file", self._ffmpeg_path)
            return self._transcribe_file(
                media_path, "Install ffmpeg to extract the audio, or upload an .srt/.vtt transcript."
            )

        with tempfile.TemporaryDirectory(prefix="aisu-audio-") as tmp:
            try:
                audio = extract_audio(
                    media_path, Path(tmp) / f"{media_path.stem}.mp3", ffmpeg, self._audio_bitrate_kbps
                )
            except AudioExtractionError as exc:
                raise TranscriptionError(f"Could not extract audio: {exc}") from exc
            return self._transcribe_file(
                audio,
                f"This is after extracting mono audio at {self._audio_bitrate_kbps} kbps; "
                "upload an .srt/.vtt transcript instead.",
            )

    def _transcribe_file(self, path: Path, too_large_hint: str) -> list[Segment]:
        size = path.stat().st_size
        if size > self.max_file_bytes:
            raise TranscriptionError(
                f"File is {size / 1024 / 1024:.1f} MB; OpenAI transcription accepts up to 25 MB. "
                + too_large_hint
            )
        try:
            with path.open("rb") as fh:
                response = self._client.post(
                    "audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data={
                        "model": self._model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                    files={"file": (path.name, fh)},
                )
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"OpenAI transcription request failed: {exc}") from exc
        if response.status_code >= 400:
            raise TranscriptionError(
                f"OpenAI transcription failed ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        segments = [
            Segment(start=float(s["start"]), end=float(s["end"]), text=s["text"].strip())
            for s in data.get("segments") or []
            if s.get("text", "").strip()
        ]
        if not segments and data.get("text", "").strip():
            segments = [Segment(start=0.0, end=float(data.get("duration") or 0), text=data["text"].strip())]
        return segments
