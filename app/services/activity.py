"""Per-learner activity aggregates; the input for learner archetypes later.

Aggregation runs in Python so local-time bucketing is identical on SQLite and
Postgres. Move it to SQL (or a rollup table) once event volumes demand it.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ContentFormat, EventType
from app.models import Learner, LearningEvent
from app.schemas import ActivitySummary, FormatStats, HourBucket, WeekActivity

_LESSON_EVENTS = {EventType.LESSON_OPEN, EventType.LESSON_COMPLETE, EventType.LESSON_SKIP}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def summarize_activity(
    db: Session,
    learner: Learner,
    since: datetime | None = None,
    until: datetime | None = None,
) -> ActivitySummary:
    tz = ZoneInfo(learner.timezone)
    query = select(
        LearningEvent.occurred_at,
        LearningEvent.event_type,
        LearningEvent.content_format,
        LearningEvent.duration_sec,
        LearningEvent.expected_duration_sec,
    ).where(LearningEvent.tenant_id == learner.tenant_id, LearningEvent.learner_id == learner.id)
    if since:
        query = query.where(LearningEvent.occurred_at >= since)
    if until:
        query = query.where(LearningEvent.occurred_at < until)
    rows = db.execute(query.order_by(LearningEvent.occurred_at)).all()

    hours = [0] * 24
    days_by_week: dict[date, set[date]] = defaultdict(set)
    lesson_counts: dict[str, Counter] = defaultdict(Counter)
    duration_ratios: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        local = row.occurred_at.astimezone(tz)
        hours[local.hour] += 1
        days_by_week[_week_start(local.date())].add(local.date())

        if row.event_type in _LESSON_EVENTS and row.content_format:
            lesson_counts[row.content_format][row.event_type] += 1
            if (
                row.event_type == EventType.LESSON_COMPLETE
                and row.duration_sec is not None
                and row.expected_duration_sec
            ):
                duration_ratios[row.content_format].append(row.duration_sec / row.expected_duration_sec)

    weekly: list[WeekActivity] = []
    if days_by_week:
        week, last_week = min(days_by_week), max(days_by_week)
        while week <= last_week:  # include inactive weeks so averages are honest
            weekly.append(WeekActivity(week_start=week, days_active=len(days_by_week.get(week, ()))))
            week += timedelta(days=7)

    formats: list[FormatStats] = []
    for fmt in ContentFormat:
        counts = lesson_counts.get(fmt.value, Counter())
        opened = counts[EventType.LESSON_OPEN]
        completed = counts[EventType.LESSON_COMPLETE]
        skipped = counts[EventType.LESSON_SKIP]
        # An LMS may report completions without a matching open, so never let
        # the denominator be smaller than the outcomes.
        attempts = max(opened, completed + skipped)
        ratios = duration_ratios.get(fmt.value)
        formats.append(
            FormatStats(
                content_format=fmt,
                lessons_opened=opened,
                lessons_completed=completed,
                lessons_skipped=skipped,
                completion_rate=_rate(completed, attempts),
                skip_rate=_rate(skipped, attempts),
                avg_duration_ratio=round(sum(ratios) / len(ratios), 4) if ratios else None,
            )
        )

    peak = max(hours)
    total_days = sum(len(days) for days in days_by_week.values())
    return ActivitySummary(
        learner_id=learner.id,
        timezone=learner.timezone,
        since=since,
        until=until,
        total_events=len(rows),
        first_event_at=rows[0].occurred_at if rows else None,
        last_event_at=rows[-1].occurred_at if rows else None,
        total_active_days=total_days,
        active_hours=[HourBucket(hour=h, event_count=c) for h, c in enumerate(hours)],
        peak_hours=[h for h, c in enumerate(hours) if c == peak] if peak else [],
        weekly_activity=weekly,
        avg_days_active_per_week=round(total_days / len(weekly), 2) if weekly else None,
        content_formats=formats,
    )
