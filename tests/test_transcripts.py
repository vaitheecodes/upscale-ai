import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.ingestion.transcript import (
    Segment,
    TranscriptParseError,
    group_segments,
    parse_timestamp,
    parse_transcript,
)
from app.providers import (
    TranscriptionError,
    TranscriptionProvider,
    build_transcription_provider,
)
from app.providers.openai import OpenAITranscriptionProvider

SRT = (
    "﻿1\r\n"
    "00:00:00,000 --> 00:00:04,500\r\n"
    "Welcome to the course.\r\n"
    "\r\n"
    "2\r\n"
    "00:00:04,500 --> 00:00:09,000\r\n"
    "Today we cover\r\n"
    "<i>gradient descent</i> &amp; friends.\r\n"
    "\r\n"
    "3\r\n"
    "00:01:05,250 --> 01:00:10,000\r\n"
    "Long tail.\r\n"
)

VTT = """WEBVTT - lecture 1

NOTE This is a comment
spanning two lines

STYLE
::cue { color: yellow }

intro
00:00.000 --> 00:03.000 align:start position:10%
<v Instructor>Hello and welcome.</v>

00:00:03.000 --> 00:00:07.500
Let's <c.highlight>begin</c>.

00:00:08.000 --> 00:00:09.000

"""


def test_parse_timestamp():
    assert parse_timestamp("00:00:01,500") == 1.5
    assert parse_timestamp("01:02:03.004") == 3723.004
    assert parse_timestamp("02:03.5") == 123.5


def test_parse_srt():
    segments = parse_transcript(SRT.encode("utf-8"))
    assert segments == [
        Segment(0.0, 4.5, "Welcome to the course."),
        Segment(4.5, 9.0, "Today we cover gradient descent & friends."),
        Segment(65.25, 3610.0, "Long tail."),
    ]


def test_parse_vtt_skips_header_notes_styles_and_markup():
    segments = parse_transcript(VTT)
    assert segments == [
        Segment(0.0, 3.0, "Hello and welcome."),
        Segment(3.0, 7.5, "Let's begin."),
    ]


def test_parse_rejects_content_without_cues():
    with pytest.raises(TranscriptParseError):
        parse_transcript("WEBVTT\n\nNOTE nothing here\n")
    with pytest.raises(TranscriptParseError):
        parse_transcript("just some text")


def test_group_segments_into_windows():
    segments = [Segment(float(s), float(s + 10), f"s{s}") for s in range(0, 150, 10)]
    chunks = group_segments(segments, window_sec=60)

    assert [(c.start_sec, c.end_sec) for c in chunks] == [(0, 60), (60, 120), (120, 150)]
    assert chunks[0].text == "s0 s10 s20 s30 s40 s50"
    assert [c.index for c in chunks] == [0, 1, 2]
    assert chunks[0].word_count == 6


def test_group_segments_keeps_long_segment_whole():
    chunks = group_segments([Segment(0, 5, "a"), Segment(5, 200, "b c"), Segment(200, 210, "d")], 60)
    assert [(c.start_sec, c.end_sec, c.text) for c in chunks] == [
        (0, 5, "a"),
        (5, 200, "b c"),
        (200, 210, "d"),
    ]
    assert group_segments([], 60) == []


def _upload_video(client, auth, transcript: tuple | None = None):
    files = {"file": ("lecture.mp4", b"\x00fake-video-bytes", "video/mp4")}
    if transcript:
        files["transcript"] = transcript
    return client.post("/sources/video", files=files, headers=auth)


def test_video_with_srt_is_chunked(client, auth):
    resp = _upload_video(client, auth, ("lecture.srt", SRT.encode(), "application/x-subrip"))
    assert resp.status_code == 201, resp.text
    source = resp.json()
    assert source["kind"] == "video"
    assert source["status"] == "ready"
    assert source["metadata"]["transcript_origin"] == "uploaded"
    assert source["metadata"]["duration_sec"] == 3610.0

    chunks = client.get(f"/sources/{source['id']}/chunks", headers=auth).json()["items"]
    assert [(c["start_sec"], c["end_sec"]) for c in chunks] == [(0.0, 9.0), (65.25, 3610.0)]
    assert chunks[0]["text"] == "Welcome to the course. Today we cover gradient descent & friends."
    assert chunks[0]["page_number"] is None


def test_video_without_transcript_needs_transcript_then_attach(client, auth):
    source = _upload_video(client, auth).json()
    assert source["status"] == "needs_transcript"
    assert client.get(f"/sources/{source['id']}/chunks", headers=auth).json()["total"] == 0

    pending = client.get("/sources", params={"status": "needs_transcript"}, headers=auth).json()
    assert [s["id"] for s in pending["items"]] == [source["id"]]

    resp = client.post(
        f"/sources/{source['id']}/transcript", files={"transcript": ("cc.vtt", VTT.encode())}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ready"
    chunks = client.get(f"/sources/{source['id']}/chunks", headers=auth).json()
    assert chunks["total"] == 1
    assert chunks["items"][0]["text"] == "Hello and welcome. Let's begin."

    # Re-attaching replaces the chunks instead of appending.
    client.post(f"/sources/{source['id']}/transcript", files={"transcript": ("cc.srt", SRT.encode())}, headers=auth)
    assert client.get(f"/sources/{source['id']}/chunks", headers=auth).json()["total"] == 2


def test_video_upload_validation(client, auth, make_pdf):
    assert _upload_video(client, auth, ("cc.txt", b"hello")).status_code == 415
    assert _upload_video(client, auth, ("cc.srt", b"no cues here")).status_code == 400
    resp = client.post("/sources/video", files={"file": ("slides.pptx", b"x")}, headers=auth)
    assert resp.status_code == 415

    pdf = client.post("/sources/pdf", files={"file": ("a.pdf", make_pdf(["text"]))}, headers=auth).json()
    resp = client.post(f"/sources/{pdf['id']}/transcript", files={"transcript": ("cc.srt", SRT.encode())}, headers=auth)
    assert resp.status_code == 409


class FakeTranscriber(TranscriptionProvider):
    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[Path] = []

    def transcribe(self, media_path: Path) -> list[Segment]:
        self.calls.append(media_path)
        if self.fail:
            raise TranscriptionError("quota exceeded")
        return [Segment(0, 30, "first half"), Segment(30, 75, "second half")]


def test_video_uses_transcription_provider(app, client, auth):
    app.state.transcriber = FakeTranscriber()
    source = _upload_video(client, auth).json()

    assert source["status"] == "ready"
    assert source["metadata"]["transcript_origin"] == "fake"
    assert app.state.transcriber.calls[0].read_bytes() == b"\x00fake-video-bytes"
    chunks = client.get(f"/sources/{source['id']}/chunks", headers=auth).json()["items"]
    assert [(c["start_sec"], c["end_sec"]) for c in chunks] == [(0, 30), (30, 75)]


def test_uploaded_transcript_wins_over_provider(app, client, auth):
    app.state.transcriber = FakeTranscriber()
    source = _upload_video(client, auth, ("cc.vtt", VTT.encode())).json()
    assert source["metadata"]["transcript_origin"] == "uploaded"
    assert app.state.transcriber.calls == []


def test_transcription_failure_marks_source_failed(app, client, auth):
    app.state.transcriber = FakeTranscriber(fail=True)
    source = _upload_video(client, auth).json()
    assert source["status"] == "failed"
    assert "quota exceeded" in source["error"]


def test_provider_selection():
    base = {"_env_file": None, "openai_api_key": None}
    assert build_transcription_provider(Settings(**base, transcription_provider="auto")) is None
    assert build_transcription_provider(Settings(**base, transcription_provider="none")) is None
    with_key = Settings(_env_file=None, openai_api_key="sk-test", transcription_provider="auto")
    assert isinstance(build_transcription_provider(with_key), OpenAITranscriptionProvider)
    off = Settings(_env_file=None, openai_api_key="sk-test", transcription_provider="none")
    assert build_transcription_provider(off) is None


def test_openai_provider_parses_segments(tmp_path):
    media = tmp_path / "talk.mp3"
    media.write_bytes(b"audio")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        body = {"text": "hi there", "segments": [{"start": 0, "end": 2.5, "text": " hi"}, {"start": 2.5, "end": 4, "text": " there "}]}
        return httpx.Response(200, content=json.dumps(body))

    client = httpx.Client(base_url="https://api.test/v1", transport=httpx.MockTransport(handler))
    provider = OpenAITranscriptionProvider(api_key="sk-test", client=client)

    assert provider.transcribe(media) == [Segment(0, 2.5, "hi"), Segment(2.5, 4, "there")]
    assert seen == {"url": "https://api.test/v1/audio/transcriptions", "auth": "Bearer sk-test"}


def test_openai_provider_wraps_http_errors(tmp_path):
    media = tmp_path / "talk.mp3"
    media.write_bytes(b"audio")
    client = httpx.Client(
        base_url="https://api.test/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(429, text="rate limited")),
    )
    with pytest.raises(TranscriptionError, match="429"):
        OpenAITranscriptionProvider(api_key="sk-test", client=client).transcribe(media)
