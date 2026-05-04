"""Autonomous Scheduler endpoints.

* POST /scheduler/daily-log               — submit a 30-second voice update
* GET  /scheduler/projects/{id}/gantt     — current Gantt rows
* POST /scheduler/projects/{id}/cpm       — force CPM recompute
* GET  /scheduler/projects/{id}/cpm/last  — most recent snapshot
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, require_permission
from app.core.rbac import Permission
from app.models.schedule import CriticalPathSnapshot, ScheduleActivity
from app.schemas.ingest import IngestHealthReportOut
from app.schemas.schedule import (
    ActivityOut,
    CriticalPathOut,
    DailyLogIn,
    DailyLogOut,
)
from app.services import critical_path, daily_log_ingest, ingest_validation
from pydantic import BaseModel

router = APIRouter()


class DailyLogResponse(BaseModel):
    log: DailyLogOut
    ingest_health: IngestHealthReportOut


@router.post(
    "/daily-log",
    response_model=DailyLogResponse,
    summary="Submit a daily log (voice-to-text)",
    description=(
        "Ingests a 30-second field update, parses progress + blockers, "
        "updates the matching Gantt activity, recomputes CPM, and pings "
        "the notification bus. The response includes an ``ingest_health`` "
        "block flagging optional fields (``crew_count``, "
        "``weather_lost_hours``) that improve Wrap Risk Score accuracy."
    ),
    dependencies=[Depends(require_permission(Permission.DAILY_LOG_SUBMIT))],
)
def submit_daily_log(
    payload: DailyLogIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.DAILY_LOG_SUBMIT)),
) -> DailyLogResponse:
    log = daily_log_ingest.ingest_log(
        db,
        project_id=payload.project_id,
        activity_id=payload.activity_id,
        submitted_by=user.email,
        raw_transcript=payload.raw_transcript,
        crew_count=payload.crew_count,
        weather_lost_hours=payload.weather_lost_hours,
    )
    health = ingest_validation.evaluate_daily_log(payload.model_dump())
    return DailyLogResponse(
        log=DailyLogOut.model_validate(log),
        ingest_health=IngestHealthReportOut(**{**health.__dict__, "grade": health.grade}),
    )


@router.get("/projects/{project_id}/gantt", response_model=list[ActivityOut])
def gantt(project_id: int, db: Session = Depends(db_session)) -> list[ActivityOut]:
    rows = (
        db.query(ScheduleActivity)
        .filter(ScheduleActivity.project_id == project_id)
        .order_by(ScheduleActivity.planned_start)
        .all()
    )
    return [ActivityOut.model_validate(r) for r in rows]


@router.post(
    "/projects/{project_id}/cpm",
    response_model=CriticalPathOut,
    dependencies=[Depends(require_permission(Permission.SCHEDULE_WRITE))],
)
def recompute_cpm(project_id: int, db: Session = Depends(db_session)) -> CriticalPathOut:
    try:
        snap = critical_path.recompute(db, project_id, trigger="manual")
    except (LookupError, ValueError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return CriticalPathOut.model_validate(snap)


@router.get("/projects/{project_id}/cpm/last", response_model=CriticalPathOut)
def last_cpm(project_id: int, db: Session = Depends(db_session)) -> CriticalPathOut:
    snap = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no snapshot yet")
    return CriticalPathOut.model_validate(snap)
