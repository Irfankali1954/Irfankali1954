"""Notification + Senior Alert recipient models."""

from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, JSON, DateTime, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Notification(Base):
    """One row per evaluation that produces an alert.

    The ``dedupe_key`` collapses repeat triggers in the same drift bucket so
    we don't text the CEO every time the cron loop runs.
    """
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    tier: Mapped[str] = mapped_column(String(16), index=True)  # tier_1|tier_2|tier_3
    trigger: Mapped[str] = mapped_column(String(64))           # e.g. cpm_recompute, idle_open
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(4000))

    cpm_drift_days: Mapped[float] = mapped_column(Float, default=0)
    open_idle_cost: Mapped[float] = mapped_column(Float, default=0)

    idle_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("idle_events.id"), nullable=True,
    )
    claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("delay_claims.id"), nullable=True,
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), index=True)
    dispatched_to: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class NotificationRecipient(Base):
    """The CFO-curated 'Senior Alert List'.

    Tier 1 recipients see dashboard rows only. Tier 2 recipients receive
    email blasts. Tier 3 recipients are SMS'd directly when nuclear thresholds
    are breached. The CFO chooses who is on which tier.
    """
    __tablename__ = "notification_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    role_label: Mapped[str] = mapped_column(String(64))   # CEO, CFO, COO, PD, etc.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tiers: Mapped[list[str]] = mapped_column(JSON, default=list)  # ["tier_2","tier_3"]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
