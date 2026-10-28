"""Audio extraction with ffmpeg, used to shrink videos before transcription."""

import subprocess
from pathlib import Path


class AudioExtractionError(Exception):
    pass


def extract_audio(
    media_path: Path,
    output_path: Path,
    ffmpeg: str = "ffmpeg",
    bitrate_kbps: int = 32,
    sample_rate: int = 16000,
    timeout_sec: float = 1800,
) -> Path:
    """Write the first audio stream of `media_path` as mono, low-bitrate MP3.

    16 kHz mono is what speech models resample to anyway, so this loses no
    transcription quality while cutting ~1 hour of audio to ~14 MB at 32 kbps.
    """
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(media_path),
        "-map", "0:a:0",  # fails clearly when the file has no audio stream
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
        str(output_path),
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except FileNotFoundError as exc:
        raise AudioExtractionError(f"ffmpeg not found: {ffmpeg}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError(f"ffmpeg timed out after {timeout_sec:g} s") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-500:] or f"exit code {result.returncode}"
        raise AudioExtractionError(f"ffmpeg failed: {detail}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioExtractionError("ffmpeg produced no audio")
    return output_path
