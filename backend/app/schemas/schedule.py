from datetime import datetime
from pydantic import BaseModel


class ActivityOut(BaseModel):
    id: int
    activity_id: str
    name: str
    wbs: str
    planned_start: datetime
    planned_finish: datetime
    actual_start: datetime | None
    actual_finish: datetime | None
    duration_days: float
    percent_complete: float
    is_critical: bool
    predecessors: list[str]
    successors: list[str]

    model_config = {"from_attributes": True}


class DailyLogIn(BaseModel):
    project_id: int
    activity_id: str
    raw_transcript: str
    crew_count: int | None = None
    weather_lost_hours: float | None = None


class DailyLogOut(BaseModel):
    id: int
    activity_id: str
    submitted_by: str
    submitted_at: datetime
    parsed_progress_pct: float | None
    parsed_blockers: list[str]

    model_config = {"from_attributes": True}


class CriticalPathOut(BaseModel):
    project_id: int
    computed_at: datetime
    project_finish: datetime
    total_float_days: float
    critical_activity_ids: list[str]
    trigger: str

    model_config = {"from_attributes": True}


class ScheduleImportResult(BaseModel):
    source: str  # "p6_xer" | "msp_mpp"
    activities_ingested: int
    project_id: int
    cpm_recomputed: bool
