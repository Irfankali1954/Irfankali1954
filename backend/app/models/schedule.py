from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, JSON, DateTime, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ScheduleActivity(Base):
    """A row from a P6 / MSP schedule. One row per activity (Gantt bar)."""
    __tablename__ = "schedule_activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)

    activity_id: Mapped[str] = mapped_column(String(64), index=True)  # P6 activity ID
    name: Mapped[str] = mapped_column(String(255))
    wbs: Mapped[str] = mapped_column(String(64))

    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    planned_finish: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_finish: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    duration_days: Mapped[float] = mapped_column(Float, default=0)
    percent_complete: Mapped[float] = mapped_column(Float, default=0)
    is_critical: Mapped[bool] = mapped_column(default=False)

    predecessors: Mapped[list[str]] = mapped_column(JSON, default=list)
    successors: Mapped[list[str]] = mapped_column(JSON, default=list)

    source: Mapped[str] = mapped_column(String(16), default="p6")  # p6 | msp | manual


class DailyLog(Base):
    """A 30-second voice-to-text update from the field, normalized."""
    __tablename__ = "daily_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(64), index=True)
    submitted_by: Mapped[str] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    raw_transcript: Mapped[str] = mapped_column(String(4000))
    parsed_progress_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    parsed_blockers: Mapped[list[str]] = mapped_column(JSON, default=list)
    crew_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weather_lost_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class CriticalPathSnapshot(Base):
    """A frozen snapshot of the CPM after each schedule recompute."""
    __tablename__ = "critical_path_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    critical_activity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    project_finish: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_float_days: Mapped[float] = mapped_column(Float, default=0)
    trigger: Mapped[str] = mapped_column(String(64), default="manual")  # daily_log | xer_import | erp_sync
