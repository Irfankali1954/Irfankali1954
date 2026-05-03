"""Field Idle Cost calculator.

When an RFC drawing is overdue, crews and equipment that depended on it sit
idle. This module computes the cost of that idleness so the Project Director
can be alerted with a real number, not 'a delay'.

Formula::

    idle_hours      = (now − rfc_due) clipped to working hours
    crew_cost       = idle_hours × idle_crew × crew_burdened_rate
    equipment_cost  = idle_hours × Σ equipment_rate(eq)
    total           = crew_cost + equipment_cost
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.risk import IdleEvent, RFCDrawing


@dataclass
class IdleCost:
    idle_hours: float
    crew_cost: float
    equipment_cost: float
    total: float


WORKING_HOURS_PER_DAY = 10.0  # configurable per-project later


def compute_idle_hours(rfc_due: datetime, until: datetime | None = None) -> float:
    until = until or datetime.now(timezone.utc)
    if until <= rfc_due:
        return 0.0
    elapsed_days = (until - rfc_due).total_seconds() / 86_400.0
    return elapsed_days * WORKING_HOURS_PER_DAY


def compute_idle_cost(
    *,
    rfc_due: datetime,
    idle_crew: int,
    crew_burdened_rate: float,
    equipment_rates: list[float],
    until: datetime | None = None,
) -> IdleCost:
    hours = compute_idle_hours(rfc_due, until)
    crew_cost = hours * idle_crew * crew_burdened_rate
    equipment_cost = hours * sum(equipment_rates)
    return IdleCost(
        idle_hours=hours,
        crew_cost=crew_cost,
        equipment_cost=equipment_cost,
        total=crew_cost + equipment_cost,
    )


def open_idle_event_for_overdue_rfc(
    db: Session,
    rfc: RFCDrawing,
    *,
    idle_crew: int,
    crew_burdened_rate: float,
    equipment: list[tuple[str, float]],
) -> IdleEvent:
    cost = compute_idle_cost(
        rfc_due=rfc.rfc_due,
        idle_crew=idle_crew,
        crew_burdened_rate=crew_burdened_rate,
        equipment_rates=[r for _, r in equipment],
    )
    evt = IdleEvent(
        project_id=rfc.project_id,
        rfc_drawing_id=rfc.id,
        cause="missing_rfc",
        started_at=rfc.rfc_due,
        idle_crew=idle_crew,
        idle_equipment=[name for name, _ in equipment],
        crew_burdened_rate=crew_burdened_rate,
        equipment_rate=sum(r for _, r in equipment),
        computed_cost=cost.total,
    )
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return evt
