from enum import StrEnum


class EventType(StrEnum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    LESSON_OPEN = "lesson_open"
    LESSON_COMPLETE = "lesson_complete"
    LESSON_SKIP = "lesson_skip"
    QUIZ_ATTEMPT = "quiz_attempt"
    HINT_USED = "hint_used"
    VIDEO_PLAY = "video_play"
    VIDEO_PAUSE = "video_pause"
    VIDEO_SEEK = "video_seek"
    TUTOR_QUESTION = "tutor_question"
    REWARD_EARNED = "reward_earned"


class ContentFormat(StrEnum):
    THEORY = "theory"
    VIDEO = "video"
    QUIZ = "quiz"
    GAME = "game"
    CODING = "coding"


class SourceKind(StrEnum):
    PDF = "pdf"
    VIDEO = "video"


class SourceStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_TRANSCRIPT = "needs_transcript"
    NEEDS_OCR = "needs_ocr"
    FAILED = "failed"
