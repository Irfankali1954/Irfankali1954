"""Master-Wrap Risk Engine.

Composite percentage representing P(hit Commercial Operation Date). A weighted
sum of five sub-factors, each in [0,1]:

* schedule_factor   — total float vs threshold (CPM-driven)
* rfc_factor        — fraction of RFC drawings on time
* permit_factor     — fraction of permits granted by target
* long_lead_factor  — long-lead supplier on-time rate
* field_idle_factor — inverse-scaled field idle cost vs. budget

Weights are tuneable; defaults below reflect typical EPC LSTK risk drivers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.financial import Project
from app.models.risk import (
    PermitStatus,
    RFCDrawing,
    IdleEvent,
    WrapScoreSnapshot,
)
from app.models.schedule import CriticalPathSnapshot


WEIGHTS = {
    "schedule": 0.30,
    "rfc": 0.25,
    "permit": 0.15,
    "long_lead": 0.15,
    "field_idle": 0.15,
}
FLOAT_THRESHOLD_DAYS = 14.0


@dataclass
class Factors:
    schedule: float
    rfc: float
    permit: float
    long_lead: float
    field_idle: float

    def composite(self) -> float:
        return 100.0 * (
            WEIGHTS["schedule"] * self.schedule
            + WEIGHTS["rfc"] * self.rfc
            + WEIGHTS["permit"] * self.permit
            + WEIGHTS["long_lead"] * self.long_lead
            + WEIGHTS["field_idle"] * self.field_idle
        )


def _schedule_factor(db: Session, project_id: int) -> float:
    snap = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    if snap is None:
        return 0.5
    if snap.total_float_days >= FLOAT_THRESHOLD_DAYS:
        return 1.0
    if snap.total_float_days <= -FLOAT_THRESHOLD_DAYS:
        return 0.0
    return 0.5 + (snap.total_float_days / (2 * FLOAT_THRESHOLD_DAYS))


def _rfc_factor(db: Session, project_id: int) -> float:
    drawings = db.query(RFCDrawing).filter(RFCDrawing.project_id == project_id).all()
    if not drawings:
        return 1.0
    on_time = sum(
        1 for d in drawings
        if d.rfc_issued is not None and d.rfc_issued <= d.rfc_due
    )
    return on_time / len(drawings)


def _permit_factor(db: Session, project_id: int) -> float:
    permits = db.query(PermitStatus).filter(PermitStatus.project_id == project_id).all()
    if not permits:
        return 1.0
    granted = sum(1 for p in permits if p.status == "granted")
    return granted / len(permits)


def _long_lead_factor(_db: Session, _project_id: int) -> float:
    # Wired once supplier-delivery model lands. Default neutral.
    return 0.8


def _field_idle_factor(db: Session, project_id: int) -> float:
    project = db.get(Project, project_id)
    if project is None or float(project.budget_total or 0) == 0:
        return 1.0
    total_idle = sum(
        float(evt.computed_cost or 0)
        for evt in db.query(IdleEvent).filter(IdleEvent.project_id == project_id).all()
    )
    ratio = total_idle / float(project.budget_total)
    return max(0.0, 1.0 - min(ratio * 20.0, 1.0))  # 5% idle ⇒ factor 0.0


def compute(db: Session, project_id: int, *, notes: str | None = None) -> WrapScoreSnapshot:
    factors = Factors(
        schedule=_schedule_factor(db, project_id),
        rfc=_rfc_factor(db, project_id),
        permit=_permit_factor(db, project_id),
        long_lead=_long_lead_factor(db, project_id),
        field_idle=_field_idle_factor(db, project_id),
    )
    snap = WrapScoreSnapshot(
        project_id=project_id,
        computed_at=datetime.now(timezone.utc),
        score=factors.composite(),
        schedule_factor=factors.schedule,
        rfc_factor=factors.rfc,
        permit_factor=factors.permit,
        long_lead_factor=factors.long_lead,
        field_idle_factor=factors.field_idle,
        notes=notes,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap
