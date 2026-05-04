"""Master-Wrap Risk Engine.

The composite ``WrapScore`` is a real-time percentage in [0, 100] expressing
P(hit Commercial Operation Date). It is the cross-pollination layer of the
agent: a delay anywhere in the project surfaces here as a number the
Project Director and CFO can act on.

Formula
=======

Five factors, each in [0, 1], weighted::

    score = 100 × ( w_sched·f_sched + w_rfc·f_rfc + w_permit·f_permit
                  + w_long_lead·f_long_lead + w_idle·f_idle )

* ``f_sched``     — clamp((total_float + T) / 2T, 0, 1) from latest CPM snap
* ``f_rfc``       — Σ severity(d) / N over all RFC drawings
                    severity = 1 if on time, else max(0, 1 − days_late/28)
* ``f_permit``    — granted / total
* ``f_long_lead`` — supplier on-time rate (stub returns 0.8)
* ``f_idle``      — clamp(1 − k · (Σ idle_cost / budget), 0, 1) with k=20

Cross-pollination flow when an RFC is missed
============================================

1. EDC connector (or :func:`simulate_rfc_miss`) flags the drawing as overdue.
2. :func:`app.services.field_idle_cost.open_idle_event_for_overdue_rfc`
   records an :class:`IdleEvent` with::

       idle_hours     = (now − rfc_due) × 10 working_hours/day
       computed_cost  = idle_hours × (idle_crew · crew_rate + Σ equipment_rate)

3. :func:`compute` is called. The new IdleEvent now contributes to
   ``f_idle`` (numerator grows, factor falls). The RFC drawing's overdue
   days also lowers ``f_rfc``. Both factors compound the score downward.
4. A new :class:`WrapScoreSnapshot` is persisted; the delta against the
   prior snapshot is returned via :func:`compute_with_delta`.
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
from datetime import timedelta
from app.models.schedule import CriticalPathSnapshot
from app.services import field_idle_cost


WEIGHTS = {
    "schedule": 0.30,
    "rfc": 0.25,
    "permit": 0.15,
    "long_lead": 0.15,
    "field_idle": 0.15,
}
FLOAT_THRESHOLD_DAYS = 14.0
RFC_GRACE_DAYS = 28.0           # 4-week decay before a single drawing scores 0
IDLE_BUDGET_PENALTY_K = 20.0    # 5% of budget burned idle ⇒ factor 0


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


# ---------- factor calculators ---------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; coerce naive datetimes to UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _schedule_factor(db: Session, project_id: int) -> float:
    snap = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    if snap is None:
        return 0.5  # unknown ⇒ neutral
    raw = (snap.total_float_days + FLOAT_THRESHOLD_DAYS) / (2 * FLOAT_THRESHOLD_DAYS)
    return _clamp(raw)


def _rfc_factor(db: Session, project_id: int, *, now: datetime | None = None) -> float:
    """Per-drawing severity: late drawings decay linearly over 28 days."""
    drawings = db.query(RFCDrawing).filter(RFCDrawing.project_id == project_id).all()
    if not drawings:
        return 1.0
    now = now or datetime.now(timezone.utc)
    score = 0.0
    for d in drawings:
        due = _aware(d.rfc_due)
        issued = _aware(d.rfc_issued) if d.rfc_issued else None
        if issued is not None and issued <= due:
            score += 1.0
            continue
        ref = issued or now
        days_late = max((ref - due).total_seconds() / 86_400.0, 0.0)
        score += _clamp(1.0 - days_late / RFC_GRACE_DAYS)
    return score / len(drawings)


def _permit_factor(db: Session, project_id: int) -> float:
    permits = db.query(PermitStatus).filter(PermitStatus.project_id == project_id).all()
    if not permits:
        return 1.0
    granted = sum(1 for p in permits if p.status == "granted")
    return granted / len(permits)


def _long_lead_factor(_db: Session, _project_id: int) -> float:
    # TODO: wire to supplier delivery model when long-lead orders land.
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
    return _clamp(1.0 - IDLE_BUDGET_PENALTY_K * ratio)


# ---------- public API ------------------------------------------------------

def factors_for(db: Session, project_id: int) -> Factors:
    return Factors(
        schedule=_schedule_factor(db, project_id),
        rfc=_rfc_factor(db, project_id),
        permit=_permit_factor(db, project_id),
        long_lead=_long_lead_factor(db, project_id),
        field_idle=_field_idle_factor(db, project_id),
    )


def compute(db: Session, project_id: int, *, notes: str | None = None) -> WrapScoreSnapshot:
    f = factors_for(db, project_id)
    snap = WrapScoreSnapshot(
        project_id=project_id,
        computed_at=datetime.now(timezone.utc),
        score=f.composite(),
        schedule_factor=f.schedule,
        rfc_factor=f.rfc,
        permit_factor=f.permit,
        long_lead_factor=f.long_lead,
        field_idle_factor=f.field_idle,
        notes=notes,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def compute_with_delta(
    db: Session, project_id: int, *, notes: str | None = None,
) -> tuple[WrapScoreSnapshot, WrapScoreSnapshot | None, float]:
    """Recompute, persist, and return ``(new, prev, delta)``.

    ``delta`` is ``new.score − prev.score``. Negative means risk got worse.
    """
    prev = (
        db.query(WrapScoreSnapshot)
        .filter(WrapScoreSnapshot.project_id == project_id)
        .order_by(WrapScoreSnapshot.computed_at.desc())
        .first()
    )
    new = compute(db, project_id, notes=notes)
    delta = new.score - prev.score if prev else 0.0
    return new, prev, delta


# ---------- simulation ------------------------------------------------------

@dataclass
class SimulationResult:
    before_score: float
    after_score: float
    delta: float
    idle_cost: float
    factors_before: Factors
    factors_after: Factors
    claim_id: int | None = None
    approval_id: int | None = None


def simulate_rfc_miss(
    db: Session,
    *,
    project_id: int,
    rfc_drawing_id: int,
    days_overdue: int = 7,
    idle_crew: int = 12,
    crew_burdened_rate: float = 120.0,
    equipment: list[tuple[str, float]] | None = None,
    auto_draft_claim: bool = True,
) -> SimulationResult:
    """Apply a synthetic RFC miss, recompute, return before/after.

    The drawing's ``rfc_due`` is back-dated by ``days_overdue``; an
    :class:`IdleEvent` is opened so ``f_idle`` and ``f_rfc`` both react.
    Used by the UI's *Simulate Delay* button to demonstrate the
    cross-pollination math without touching real ERP/EDC data.
    """
    rfc = db.get(RFCDrawing, rfc_drawing_id)
    if rfc is None:
        raise LookupError("rfc drawing not found")

    # Snapshot factors before any mutation.
    before = factors_for(db, project_id)

    # Mutate: backdate the due date and clear any prior issuance.
    rfc.rfc_due = datetime.now(timezone.utc) - timedelta(days=days_overdue)
    rfc.rfc_issued = None
    db.add(rfc)
    db.flush()

    idle_event = field_idle_cost.open_idle_event_for_overdue_rfc(
        db, rfc,
        idle_crew=idle_crew,
        crew_burdened_rate=crew_burdened_rate,
        equipment=equipment or [("crane_LR1300", 950.0), ("excavator_345", 410.0)],
    )

    after_snap = compute(db, project_id, notes=f"sim:rfc_miss drawing={rfc.drawing_no}")
    after = Factors(
        schedule=after_snap.schedule_factor,
        rfc=after_snap.rfc_factor,
        permit=after_snap.permit_factor,
        long_lead=after_snap.long_lead_factor,
        field_idle=after_snap.field_idle_factor,
    )

    claim_id: int | None = None
    approval_id: int | None = None
    if auto_draft_claim:
        # Local import to avoid a service-level circular dependency.
        from app.services import claim_harvester
        claim = claim_harvester.harvest_for_idle_event(db, idle_event.id)
        claim_id = claim.id
        approval_id = claim.approval_id

    return SimulationResult(
        before_score=before.composite(),
        after_score=after.composite(),
        delta=after.composite() - before.composite(),
        idle_cost=float(idle_event.computed_cost or 0),
        factors_before=before,
        factors_after=after,
        claim_id=claim_id,
        approval_id=approval_id,
    )


def simulate_permit_delay(
    db: Session,
    *,
    project_id: int,
    permit_id: int,
    days_overdue: int = 7,
    idle_crew: int = 18,
    crew_burdened_rate: float = 130.0,
    equipment: list[tuple[str, float]] | None = None,
    auto_draft_claim: bool = True,
) -> SimulationResult:
    """Permit-delay sibling of :func:`simulate_rfc_miss`.

    Backdates ``permit.target_date``, leaves status non-granted, opens an
    :class:`IdleEvent` linked via ``permit_id``, recomputes the wrap score,
    and (by default) auto-drafts a Delay-Claim. The harvester resolves the
    permit subject from ``IdleEvent.permit_id`` directly — no heuristic.
    """
    permit = db.get(PermitStatus, permit_id)
    if permit is None:
        raise LookupError("permit not found")

    before = factors_for(db, project_id)

    permit.target_date = datetime.now(timezone.utc) - timedelta(days=days_overdue)
    if permit.status == "granted":
        permit.status = "pending"
    permit.granted_date = None
    db.add(permit)
    db.flush()

    idle_event = field_idle_cost.open_idle_event_for_overdue_permit(
        db, permit,
        idle_crew=idle_crew,
        crew_burdened_rate=crew_burdened_rate,
        equipment=equipment or [("crane_LR1300", 950.0), ("welding_rig", 220.0)],
    )

    after_snap = compute(db, project_id, notes=f"sim:permit_delay {permit.permit_type}")
    after = Factors(
        schedule=after_snap.schedule_factor,
        rfc=after_snap.rfc_factor,
        permit=after_snap.permit_factor,
        long_lead=after_snap.long_lead_factor,
        field_idle=after_snap.field_idle_factor,
    )

    claim_id: int | None = None
    approval_id: int | None = None
    if auto_draft_claim:
        from app.services import claim_harvester
        claim = claim_harvester.harvest_for_idle_event(db, idle_event.id)
        claim_id = claim.id
        approval_id = claim.approval_id

    return SimulationResult(
        before_score=before.composite(),
        after_score=after.composite(),
        delta=after.composite() - before.composite(),
        idle_cost=float(idle_event.computed_cost or 0),
        factors_before=before,
        factors_after=after,
        claim_id=claim_id,
        approval_id=approval_id,
    )
