"""Heatmap dwell-time tracking.

One row per (project_id, activity_id). The row records the activity's
current quadrant in the Risk × Exposure plane, when it entered that
quadrant, and when we last alerted on it. The dwell-time alert fires
once per 24-hour window per HH cell that has been there ≥ 48 hours.
"""

from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class HeatmapPosition(Base):
    __tablename__ = "heatmap_positions"
    __table_args__ = (UniqueConstraint("project_id", "activity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(64), index=True)

    quadrant: Mapped[str] = mapped_column(String(2))   # "HH" | "HL" | "LH" | "LL"
    risk_impact: Mapped[float] = mapped_column(Float, default=0)
    net_exposure: Mapped[float] = mapped_column(Float, default=0)

    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
