import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.ingestion.audio import AudioExtractionError, extract_audio
from app.providers import TranscriptionError, build_transcription_provider
from app.providers.openai import OpenAITranscriptionProvider

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

WHISPER_REPLY = {"text": "hello", "segments": [{"start": 0, "end": 3, "text": "hello"}]}


def _make_clip(path: Path, with_audio: bool = True, seconds: int = 3) -> Path:
    """Generate a stereo 44.1 kHz video with a high video bitrate (large file, short audio)."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=640x480:rate=25:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}", "-ac", "2"]
    cmd += ["-c:v", "mpeg4", "-b:v", "4M", "-shortest", str(path)]  # fmt: skip
    subprocess.run(cmd, check=True)
    return path


def _probe_audio(path: Path) -> dict:
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return json.loads(out.stdout)["streams"]


def _recording_client(calls: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        request.read()
        calls.append(request)
        return httpx.Response(200, content=json.dumps(WHISPER_REPLY))

    return httpx.Client(base_url="https://api.test/v1", transport=httpx.MockTransport(handler))


@needs_ffmpeg
def test_extract_audio_mono_16k_mp3(tmp_path):
    clip = _make_clip(tmp_path / "lecture.mp4")
    audio = extract_audio(clip, tmp_path / "lecture.mp3", FFMPEG, bitrate_kbps=32)

    streams = _probe_audio(audio)
    assert len(streams) == 1
    stream = streams[0]
    assert stream["codec_type"] == "audio" and stream["codec_name"] == "mp3"
    assert stream["channels"] == 1
    assert stream["sample_rate"] == "16000"
    assert int(stream["bit_rate"]) <= 40_000
    assert audio.stat().st_size < clip.stat().st_size / 10


@needs_ffmpeg
def test_extract_audio_errors(tmp_path):
    silent = _make_clip(tmp_path / "silent.mp4", with_audio=False)
    with pytest.raises(AudioExtractionError, match="ffmpeg failed"):
        extract_audio(silent, tmp_path / "out.mp3", FFMPEG)
    with pytest.raises(AudioExtractionError, match="not found"):
        extract_audio(silent, tmp_path / "out.mp3", "/nonexistent/ffmpeg")


@needs_ffmpeg
def test_provider_sends_extracted_audio_when_video_is_too_large(tmp_path):
    clip = _make_clip(tmp_path / "lecture.mp4")
    calls: list[httpx.Request] = []
    provider = OpenAITranscriptionProvider("sk-test", client=_recording_client(calls), ffmpeg_path=FFMPEG)
    # The video itself is over the limit; only its extracted audio fits.
    provider.max_file_bytes = 100_000
    assert clip.stat().st_size > provider.max_file_bytes

    assert [s.text for s in provider.transcribe(clip)] == ["hello"]
    body = calls[0].content
    assert b'filename="lecture.mp3"' in body
    assert len(body) < provider.max_file_bytes


@needs_ffmpeg
def test_provider_fails_only_when_extracted_audio_is_still_too_large(tmp_path):
    clip = _make_clip(tmp_path / "lecture.mp4")
    calls: list[httpx.Request] = []
    provider = OpenAITranscriptionProvider("sk-test", client=_recording_client(calls), ffmpeg_path=FFMPEG)
    provider.max_file_bytes = 1_000

    with pytest.raises(TranscriptionError, match="after extracting mono audio at 32 kbps"):
        provider.transcribe(clip)
    assert calls == []


@needs_ffmpeg
def test_provider_reports_videos_without_audio(tmp_path):
    silent = _make_clip(tmp_path / "silent.mp4", with_audio=False)
    provider = OpenAITranscriptionProvider("sk-test", client=_recording_client([]), ffmpeg_path=FFMPEG)
    with pytest.raises(TranscriptionError, match="Could not extract audio"):
        provider.transcribe(silent)


def test_provider_falls_back_to_original_without_ffmpeg(tmp_path):
    media = tmp_path / "talk.m4a"
    media.write_bytes(b"audio")
    calls: list[httpx.Request] = []
    provider = OpenAITranscriptionProvider(
        "sk-test", client=_recording_client(calls), ffmpeg_path="definitely-not-ffmpeg"
    )
    provider.transcribe(media)
    assert b'filename="talk.m4a"' in calls[0].content

    provider.max_file_bytes = 1
    with pytest.raises(TranscriptionError, match="Install ffmpeg"):
        provider.transcribe(media)


def test_factory_configures_ffmpeg():
    provider = build_transcription_provider(
        Settings(_env_file=None, openai_api_key="sk-test", ffmpeg_path="/opt/ffmpeg", transcription_audio_bitrate_kbps=24)
    )
    assert provider._ffmpeg_path == "/opt/ffmpeg" and provider._audio_bitrate_kbps == 24
    no_ffmpeg = build_transcription_provider(Settings(_env_file=None, openai_api_key="sk-test", ffmpeg_path=""))
    assert no_ffmpeg._ffmpeg_path is None
