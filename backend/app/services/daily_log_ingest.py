"""Daily Log → Gantt → CPM pipeline.

A 30-second voice-to-text update from the field is normalized, the matching
``ScheduleActivity`` is updated, and the Critical Path is recomputed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.schedule import DailyLog, ScheduleActivity
from app.services import critical_path


_PCT_RE = re.compile(r"(\d{1,3})\s?%")
_BLOCKER_RE = re.compile(r"(?:blocker|waiting on|blocked by)\s*[:\-]?\s*([^.]{3,80})", re.I)


def parse_transcript(transcript: str) -> tuple[float | None, list[str]]:
    """Lightweight rule-based extraction. Replace with NLU in production."""
    pct_match = _PCT_RE.search(transcript)
    pct = float(pct_match.group(1)) if pct_match else None
    blockers = [b.strip() for b in _BLOCKER_RE.findall(transcript)]
    return pct, blockers


def ingest_log(
    db: Session,
    *,
    project_id: int,
    activity_id: str,
    submitted_by: str,
    raw_transcript: str,
    crew_count: int | None = None,
    weather_lost_hours: float | None = None,
) -> DailyLog:
    pct, blockers = parse_transcript(raw_transcript)

    log = DailyLog(
        project_id=project_id,
        activity_id=activity_id,
        submitted_by=submitted_by,
        raw_transcript=raw_transcript,
        parsed_progress_pct=pct,
        parsed_blockers=blockers,
        crew_count=crew_count,
        weather_lost_hours=weather_lost_hours,
    )
    db.add(log)

    activity = (
        db.query(ScheduleActivity)
        .filter(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.activity_id == activity_id,
        )
        .one_or_none()
    )
    if activity is not None and pct is not None:
        activity.percent_complete = max(activity.percent_complete or 0.0, pct)
        if pct >= 100 and activity.actual_finish is None:
            activity.actual_finish = datetime.now(timezone.utc)
        if activity.actual_start is None and pct > 0:
            activity.actual_start = datetime.now(timezone.utc)

    db.commit()
    db.refresh(log)

    # Trigger CPM recompute. Failures here must not lose the daily log.
    try:
        critical_path.recompute(db, project_id, trigger="daily_log")
    except Exception:  # pragma: no cover — TODO: structured logging
        pass

    return log
