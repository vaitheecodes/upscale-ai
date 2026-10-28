def ev(event_type: str, occurred_at: str, **extra) -> dict:
    return {"external_user_id": "u1", "event_type": event_type, "occurred_at": occurred_at, **extra}


# All times UTC. Learner timezone Asia/Kolkata = UTC+05:30.
EVENTS = [
    ev("session_start", "2026-03-02T03:30:00Z"),  # Mon 09:00 IST
    ev("lesson_open", "2026-03-02T04:00:00Z", lesson_id="V1", content_format="video"),  # Mon 09:30
    ev(
        "lesson_complete",
        "2026-03-02T04:10:00Z",
        lesson_id="V1",
        content_format="video",
        duration_sec=600,
        expected_duration_sec=500,
    ),  # Mon 09:40
    ev("lesson_open", "2026-03-03T19:00:00Z", lesson_id="Q1", content_format="quiz"),  # Wed 00:30
    ev("lesson_skip", "2026-03-03T19:05:00Z", lesson_id="Q1", content_format="quiz"),  # Wed 00:35
    ev("lesson_open", "2026-03-04T14:30:00Z", lesson_id="T1", content_format="theory"),  # Wed 20:00
    ev(
        "lesson_complete",
        "2026-03-04T14:45:00Z",
        lesson_id="T1",
        content_format="theory",
        duration_sec=300,
        expected_duration_sec=600,
    ),  # Wed 20:15
    ev("lesson_open", "2026-03-04T15:00:00Z", lesson_id="V2", content_format="video"),  # Wed 20:30
    ev("lesson_skip", "2026-03-04T15:01:00Z", lesson_id="V2", content_format="video"),  # Wed 20:31
    # Nothing in the week of Mar 9.
    ev("lesson_open", "2026-03-16T05:30:00Z", lesson_id="C1", content_format="coding"),  # Mon 11:00
]


def seed(client, auth, timezone_name: str | None = None) -> str:
    payload = {"external_user_id": "u1", "display_name": "Asha"}
    if timezone_name:
        payload["timezone"] = timezone_name
    learner = client.post("/learners", json=payload, headers=auth).json()
    assert client.post("/events", json={"events": EVENTS}, headers=auth).status_code == 202
    return learner["id"]


def test_activity_summary_in_learner_timezone(client, auth):
    learner_id = seed(client, auth)
    resp = client.get(f"/learners/{learner_id}/activity-summary", headers=auth)
    assert resp.status_code == 200, resp.text
    summary = resp.json()

    assert summary["timezone"] == "Asia/Kolkata"
    assert summary["total_events"] == 10
    hours = {b["hour"]: b["event_count"] for b in summary["active_hours"] if b["event_count"]}
    assert hours == {0: 2, 9: 3, 11: 1, 20: 4}
    assert len(summary["active_hours"]) == 24
    assert summary["peak_hours"] == [20]

    # The 19:00Z Tuesday events fall on Wednesday in IST, so week 1 has two active days.
    assert summary["weekly_activity"] == [
        {"week_start": "2026-03-02", "days_active": 2},
        {"week_start": "2026-03-09", "days_active": 0},
        {"week_start": "2026-03-16", "days_active": 1},
    ]
    assert summary["total_active_days"] == 3
    assert summary["avg_days_active_per_week"] == 1.0

    formats = {f["content_format"]: f for f in summary["content_formats"]}
    assert set(formats) == {"theory", "video", "quiz", "game", "coding"}
    assert formats["video"] == {
        "content_format": "video",
        "lessons_opened": 2,
        "lessons_completed": 1,
        "lessons_skipped": 1,
        "completion_rate": 0.5,
        "skip_rate": 0.5,
        "avg_duration_ratio": 1.2,
    }
    assert (formats["quiz"]["completion_rate"], formats["quiz"]["skip_rate"]) == (0.0, 1.0)
    assert (formats["theory"]["completion_rate"], formats["theory"]["avg_duration_ratio"]) == (1.0, 0.5)
    assert (formats["coding"]["completion_rate"], formats["coding"]["skip_rate"]) == (0.0, 0.0)
    assert formats["game"]["completion_rate"] is None and formats["game"]["lessons_opened"] == 0


def test_activity_summary_respects_other_timezones(client, auth):
    # New York is UTC-5 until DST starts on 2026-03-08, then UTC-4.
    learner_id = seed(client, auth, timezone_name="America/New_York")
    summary = client.get(f"/learners/{learner_id}/activity-summary", headers=auth).json()

    hours = {b["hour"]: b["event_count"] for b in summary["active_hours"] if b["event_count"]}
    assert hours == {22: 1, 23: 2, 14: 2, 9: 2, 10: 2, 1: 1}
    # Mar 2 03:30Z is Sunday Mar 1 in New York, so the first ISO week starts Feb 23.
    assert summary["weekly_activity"] == [
        {"week_start": "2026-02-23", "days_active": 1},
        {"week_start": "2026-03-02", "days_active": 2},
        {"week_start": "2026-03-09", "days_active": 0},
        {"week_start": "2026-03-16", "days_active": 1},
    ]


def test_activity_summary_time_window(client, auth):
    learner_id = seed(client, auth)
    params = {"since": "2026-03-04T00:00:00Z", "until": "2026-03-10T00:00:00Z"}
    summary = client.get(f"/learners/{learner_id}/activity-summary", params=params, headers=auth).json()
    assert summary["total_events"] == 4
    assert summary["since"].startswith("2026-03-04T00:00:00")


def test_activity_summary_without_events(client, auth):
    learner = client.post("/learners", json={"external_user_id": "quiet"}, headers=auth).json()
    summary = client.get(f"/learners/{learner['id']}/activity-summary", headers=auth).json()
    assert summary["total_events"] == 0
    assert summary["peak_hours"] == [] and summary["weekly_activity"] == []
    assert summary["avg_days_active_per_week"] is None
    assert all(f["completion_rate"] is None for f in summary["content_formats"])


def test_completion_rate_capped_when_open_missing(client, auth):
    learner = client.post("/learners", json={"external_user_id": "u1"}, headers=auth).json()
    client.post(
        "/events",
        json={"events": [ev("lesson_complete", "2026-03-02T04:00:00Z", content_format="game")] * 2},
        headers=auth,
    )
    summary = client.get(f"/learners/{learner['id']}/activity-summary", headers=auth).json()
    game = next(f for f in summary["content_formats"] if f["content_format"] == "game")
    assert game["completion_rate"] == 1.0


def test_activity_summary_is_tenant_scoped(client, auth, other_auth):
    learner_id = seed(client, auth)
    assert client.get(f"/learners/{learner_id}/activity-summary", headers=other_auth).status_code == 404
    assert client.get(f"/learners/{learner_id}", headers=other_auth).status_code == 404


def test_learner_upsert_and_timezone_validation(client, auth):
    created = client.post("/learners", json={"external_user_id": "u9"}, headers=auth)
    assert created.status_code == 201
    updated = client.post(
        "/learners", json={"external_user_id": "u9", "display_name": "Ravi", "timezone": "Europe/London"}, headers=auth
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"]
    assert updated.json()["timezone"] == "Europe/London"
    # Omitting the timezone on update keeps the stored one.
    again = client.post("/learners", json={"external_user_id": "u9"}, headers=auth).json()
    assert again["timezone"] == "Europe/London"

    bad = client.post("/learners", json={"external_user_id": "u10", "timezone": "Mars/Olympus"}, headers=auth)
    assert bad.status_code == 422
