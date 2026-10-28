import uuid


def event(**overrides) -> dict:
    return {
        "external_user_id": "moodle-42",
        "event_type": "lesson_open",
        "occurred_at": "2026-03-02T10:00:00Z",
        **overrides,
    }


def post(client, auth, *events):
    return client.post("/events", json={"events": list(events)}, headers=auth)


def test_batch_ingest_provisions_learner(client, auth):
    resp = post(
        client,
        auth,
        event(session_id="s1", lesson_id="L1", topic_id="T1", content_format="video"),
        event(
            event_type="lesson_complete",
            occurred_at="2026-03-02T10:12:00+05:30",
            lesson_id="L1",
            content_format="video",
            duration_sec=720,
            expected_duration_sec=600,
            score=0.8,
            properties={"attempt": 1},
        ),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == 2 and body["duplicates"] == 0
    assert len(body["statement_ids"]) == 2

    learners = client.get("/learners", params={"external_user_id": "moodle-42"}, headers=auth).json()
    assert learners["total"] == 1
    learner = learners["items"][0]
    assert learner["timezone"] == "Asia/Kolkata"

    stored = client.get("/events", params={"learner_id": learner["id"]}, headers=auth).json()
    assert stored["total"] == 2
    # Offsets are normalised to UTC: 10:12+05:30 == 04:42Z, so it sorts first.
    first, second = stored["items"]
    assert first["event_type"] == "lesson_complete"
    assert first["occurred_at"].startswith("2026-03-02T04:42:00")
    assert first["score"] == 0.8 and first["properties"] == {"attempt": 1}
    assert second["session_id"] == "s1"


def test_events_by_internal_learner_id(client, auth):
    learner = client.post("/learners", json={"external_user_id": "u1"}, headers=auth).json()
    resp = post(client, auth, {"learner_id": learner["id"], "event_type": "hint_used", "occurred_at": "2026-03-02T10:00:00"})
    assert resp.status_code == 202
    stored = client.get("/events", params={"learner_id": learner["id"]}, headers=auth).json()["items"]
    # Naive timestamps are treated as UTC.
    assert stored[0]["occurred_at"].startswith("2026-03-02T10:00:00")


def test_retry_with_same_statement_ids_is_idempotent(client, auth):
    sid = str(uuid.uuid4())
    first = post(client, auth, event(statement_id=sid), event())
    assert first.json()["accepted"] == 2

    retry = post(client, auth, event(statement_id=sid), event(statement_id=sid))
    assert retry.json() == {"accepted": 0, "duplicates": 2, "statement_ids": [sid, sid]}
    assert client.get("/events", headers=auth).json()["total"] == 2


def test_validation_errors(client, auth):
    cases = [
        event(event_type="lesson_teleport"),
        event(content_format="podcast"),
        event(score=1.5),
        event(duration_sec=-1),
        event(unknown_field=True),
        {"event_type": "lesson_open", "occurred_at": "2026-03-02T10:00:00Z"},
        event(statement_id="not-a-uuid"),
    ]
    for case in cases:
        assert post(client, auth, case).status_code == 422, case
    assert client.post("/events", json={"events": []}, headers=auth).status_code == 422
    assert client.get("/events", headers=auth).json()["total"] == 0


def test_unknown_learner_rejects_whole_batch(client, auth):
    resp = post(client, auth, event(), event(external_user_id=None, learner_id="missing"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["index"] == 1
    assert client.get("/events", headers=auth).json()["total"] == 0
    assert client.get("/learners", headers=auth).json()["total"] == 0


def test_learner_id_and_external_id_must_agree(client, auth):
    learner = client.post("/learners", json={"external_user_id": "u1"}, headers=auth).json()
    resp = post(client, auth, event(learner_id=learner["id"], external_user_id="someone-else"))
    assert resp.status_code == 422


def test_events_are_tenant_isolated(client, auth, other_auth):
    learner = client.post("/learners", json={"external_user_id": "u1"}, headers=auth).json()

    # Tenant B cannot write events for tenant A's learner.
    resp = post(client, other_auth, event(external_user_id=None, learner_id=learner["id"]))
    assert resp.status_code == 422

    # The same external id in tenant B is a different learner.
    post(client, other_auth, event(external_user_id="u1"))
    b_learners = client.get("/learners", headers=other_auth).json()["items"]
    assert b_learners[0]["id"] != learner["id"]
    assert client.get("/events", headers=auth).json()["total"] == 0
    assert client.get("/events", headers=other_auth).json()["total"] == 1


def test_events_require_api_key(client):
    assert client.post("/events", json={"events": [event()]}).status_code == 401


def test_batch_size_limit(app, client, auth):
    app.state.settings = app.state.settings.model_copy(update={"max_events_per_batch": 2})
    resp = client.post("/events", json={"events": [event()] * 3}, headers=auth)
    assert resp.status_code == 422


def test_event_filters(client, auth):
    post(
        client,
        auth,
        event(occurred_at="2026-03-01T00:00:00Z"),
        event(occurred_at="2026-03-05T00:00:00Z", event_type="quiz_attempt"),
        event(occurred_at="2026-03-09T00:00:00Z"),
    )
    params = {"since": "2026-03-02T00:00:00Z", "until": "2026-03-09T00:00:00Z"}
    assert client.get("/events", params=params, headers=auth).json()["total"] == 1
    assert client.get("/events", params={"event_type": "lesson_open"}, headers=auth).json()["total"] == 2
    page = client.get("/events", params={"limit": 2, "offset": 2}, headers=auth).json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_xapi_statement_view(client, auth):
    client.post("/learners", json={"external_user_id": "moodle-42", "display_name": "Asha"}, headers=auth)
    session = str(uuid.uuid4())
    post(
        client,
        auth,
        event(
            event_type="lesson_complete",
            session_id=session,
            lesson_id="L 1",
            topic_id="T1",
            content_format="quiz",
            duration_sec=90.5,
            score=0.75,
            properties={"success": True},
        ),
        event(event_type="hint_used", occurred_at="2026-03-02T11:00:00Z"),
    )
    body = client.get("/xapi/statements", params={"limit": 1}, headers=auth).json()
    assert body["more"].endswith("offset=1")
    statement = body["statements"][0]

    assert statement["actor"] == {
        "objectType": "Agent",
        "name": "Asha",
        "account": {"homePage": "https://moodle-a.example.edu", "name": "moodle-42"},
    }
    assert statement["verb"]["id"] == "http://adlnet.gov/expapi/verbs/completed"
    assert statement["object"]["id"].endswith("/lessons/L%201")
    assert statement["object"]["definition"]["type"] == "http://adlnet.gov/expapi/activities/assessment"
    assert statement["result"] == {
        "score": {"scaled": 0.75},
        "duration": "PT90.5S",
        "completion": True,
        "success": True,
    }
    assert statement["context"]["registration"] == session
    assert statement["context"]["contextActivities"]["parent"][0]["id"].endswith("/topics/T1")
    assert statement["timestamp"].startswith("2026-03-02T10:00:00")

    second = client.get("/xapi/statements", params={"offset": 1}, headers=auth).json()
    assert second["more"] == ""
    hint = second["statements"][0]
    assert hint["verb"]["id"] == "https://ai-skill-upscaler.example/xapi/verbs/hint-used"
    assert "result" not in hint
