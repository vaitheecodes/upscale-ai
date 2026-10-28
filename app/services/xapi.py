"""Mapping between learning events and xAPI 1.0.3 statements.

Standard ADL / video-profile / TinCan registry verbs are used where one fits;
the rest get ids under the configured XAPI_BASE_IRI. An inbound mapper for
Moodle's xAPI logstore can use VERB_IRIS in reverse.
"""

import uuid
from typing import Any
from urllib.parse import quote

from app.enums import ContentFormat, EventType
from app.models import Learner, LearningEvent, Tenant

XAPI_VERSION = "1.0.3"

# None = custom verb under the base IRI.
VERB_IRIS: dict[EventType, str | None] = {
    EventType.SESSION_START: "http://adlnet.gov/expapi/verbs/initialized",
    EventType.SESSION_END: "http://adlnet.gov/expapi/verbs/terminated",
    EventType.LESSON_OPEN: "http://adlnet.gov/expapi/verbs/launched",
    EventType.LESSON_COMPLETE: "http://adlnet.gov/expapi/verbs/completed",
    EventType.LESSON_SKIP: "http://id.tincanapi.com/verb/skipped",
    EventType.QUIZ_ATTEMPT: "http://adlnet.gov/expapi/verbs/attempted",
    EventType.HINT_USED: None,
    EventType.VIDEO_PLAY: "https://w3id.org/xapi/video/verbs/played",
    EventType.VIDEO_PAUSE: "https://w3id.org/xapi/video/verbs/paused",
    EventType.VIDEO_SEEK: "https://w3id.org/xapi/video/verbs/seeked",
    EventType.TUTOR_QUESTION: "http://adlnet.gov/expapi/verbs/asked",
    EventType.REWARD_EARNED: None,
}

ACTIVITY_TYPES: dict[ContentFormat, str | None] = {
    ContentFormat.THEORY: "http://adlnet.gov/expapi/activities/lesson",
    ContentFormat.VIDEO: "https://w3id.org/xapi/video/activity-type/video",
    ContentFormat.QUIZ: "http://adlnet.gov/expapi/activities/assessment",
    ContentFormat.GAME: None,
    ContentFormat.CODING: None,
}


def verb_iri(event_type: EventType, base_iri: str) -> str:
    return VERB_IRIS[event_type] or f"{base_iri}/verbs/{event_type.value.replace('_', '-')}"


def _activity_type(content_format: str | None, base_iri: str) -> str:
    if content_format is None:
        return "http://adlnet.gov/expapi/activities/lesson"
    fmt = ContentFormat(content_format)
    return ACTIVITY_TYPES[fmt] or f"{base_iri}/activity-types/{fmt.value}"


def _iso_duration(seconds: float) -> str:
    return f"PT{f'{seconds:.2f}'.rstrip('0').rstrip('.')}S"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def to_statement(event: LearningEvent, learner: Learner, tenant: Tenant, base_iri: str) -> dict[str, Any]:
    tenant_iri = f"{base_iri}/tenants/{tenant.id}"
    event_type = EventType(event.event_type)

    actor: dict[str, Any] = {
        "objectType": "Agent",
        "account": {"homePage": tenant.lms_base_url or tenant_iri, "name": learner.external_user_id},
    }
    if learner.display_name:
        actor["name"] = learner.display_name

    topic_iri = f"{tenant_iri}/topics/{quote(event.topic_id, safe='')}" if event.topic_id else None
    if event.lesson_id:
        obj_id = f"{tenant_iri}/lessons/{quote(event.lesson_id, safe='')}"
        obj_type = _activity_type(event.content_format, base_iri)
    elif topic_iri:
        obj_id, obj_type = topic_iri, "http://adlnet.gov/expapi/activities/module"
    elif event.session_id:
        obj_id = f"{tenant_iri}/sessions/{quote(event.session_id, safe='')}"
        obj_type = f"{base_iri}/activity-types/session"
    else:
        obj_id, obj_type = tenant_iri, f"{base_iri}/activity-types/platform"

    result: dict[str, Any] = {}
    if event.score is not None:
        result["score"] = {"scaled": event.score}
    if event.duration_sec is not None:
        result["duration"] = _iso_duration(event.duration_sec)
    if event_type == EventType.LESSON_COMPLETE:
        result["completion"] = True
    elif event_type == EventType.LESSON_SKIP:
        result["completion"] = False
    if isinstance(event.properties.get("success"), bool):
        result["success"] = event.properties["success"]

    ext = f"{base_iri}/extensions"
    extensions: dict[str, Any] = {f"{ext}/event-type": event_type.value}
    if event.session_id:
        extensions[f"{ext}/session-id"] = event.session_id
    if event.content_format:
        extensions[f"{ext}/content-format"] = event.content_format
    if event.expected_duration_sec is not None:
        extensions[f"{ext}/expected-duration-sec"] = event.expected_duration_sec
    if event.properties:
        extensions[f"{ext}/properties"] = event.properties

    context: dict[str, Any] = {"platform": "AI Skill Upscaler", "extensions": extensions}
    if event.session_id and _is_uuid(event.session_id):
        context["registration"] = event.session_id
    if topic_iri and event.lesson_id:
        context["contextActivities"] = {"parent": [{"objectType": "Activity", "id": topic_iri}]}

    statement: dict[str, Any] = {
        "id": event.statement_id,
        "version": XAPI_VERSION,
        "actor": actor,
        "verb": {
            "id": verb_iri(event_type, base_iri),
            "display": {"en-US": event_type.value.replace("_", " ")},
        },
        "object": {"objectType": "Activity", "id": obj_id, "definition": {"type": obj_type}},
        "context": context,
        "timestamp": event.occurred_at.isoformat(),
        "stored": event.received_at.isoformat(),
    }
    if result:
        statement["result"] = result
    return statement
