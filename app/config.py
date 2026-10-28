from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the API runs without Docker.
    # Postgres example: postgresql+psycopg://upscaler:upscaler@localhost:5432/upscaler
    database_url: str = "sqlite:///./data/app.db"
    upload_dir: Path = Path("./data/uploads")
    max_upload_mb: int = 500

    # Protects /admin/* (tenant provisioning). Admin API is disabled when unset.
    admin_api_key: str | None = None

    pdf_chunk_words: int = 300
    pdf_chunk_overlap: int = 50
    transcript_chunk_seconds: float = 60.0

    # "auto" uses OpenAI Whisper when OPENAI_API_KEY is set; "none" disables transcription.
    transcription_provider: str = "auto"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_transcription_model: str = "whisper-1"
    # Audio is extracted with ffmpeg (mono, 16 kHz MP3) before transcription.
    # Empty disables extraction; the original file is sent if it is under 25 MB.
    ffmpeg_path: str = "ffmpeg"
    transcription_audio_bitrate_kbps: int = 32

    # Base IRI for xAPI activity/extension ids. Set this to your deployed domain.
    xapi_base_iri: str = "https://ai-skill-upscaler.example/xapi"
    max_events_per_batch: int = 1000

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
