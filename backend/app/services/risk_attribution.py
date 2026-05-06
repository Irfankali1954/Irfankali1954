"""Risk Attribution.

Decomposes the project-level Wrap Risk Score *loss* into per-activity
contributions, in score points (out of 100). The math is a deterministic
linear decomposition that mirrors the composite formula in
:mod:`app.services.wrap_risk` exactly, so

    sum(activity.risk_impact for activity in attributions)  ≈  100 − wrap_score

up to a small residual covering project-wide signals that aren't tied to
a specific activity (e.g. ``long_lead_factor``, plus any RFC/permit
without a discoverable activity link).

Per-activity contribution
=========================

For each activity ``A``, we compute its share of each *factor loss*
``loss_X = 1 − f_X``, then weight by the same WEIGHTS the composite uses::

    impact(A) = 100 × (
          w_sched    · loss_sched(A)
        + w_rfc      · loss_rfc(A)
        + w_permit   · loss_permit(A)
        + w_idle     · loss_idle(A)
    )

Per-factor share derivations
----------------------------

* ``loss_rfc(A)``:    Σ over drawings d linked to A of ``(1 − severity(d)) / N_drawings``
* ``loss_permit(A)``: Σ over permits p  linked to A of ``(1 − granted(p))  / N_permits``
* ``loss_idle(A)``:   ``min(1, K · idle_cost(A) / project.budget_total)``
* ``loss_sched(A)``:  if A is on the critical path AND has slipped,
                      its share of the total schedule loss; otherwise 0

Linkage
-------

Activity ↔ artifact linkage flows through ``DelayClaim.linked_activity_id``
(populated by the harvester). So a drawing ``d`` is "linked to A" when
some active DelayClaim on A references ``d`` via ``rfc_drawing_id``.

This keeps the attribution faithful to the same evidence chain the
Statement of Facts and the Convergence dashboard already cite.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.financial import Project
from app.models.risk import DelayClaim, IdleEvent, PermitStatus, RFCDrawing
from app.models.schedule import CriticalPathSnapshot, ScheduleActivity
from app.services.wrap_risk import (
    IDLE_BUDGET_PENALTY_K,
    RFC_GRACE_DAYS,
    WEIGHTS,
    factors_for,
)


@dataclass
class ActivityRiskContribution:
    activity_id: str
    risk_impact: float            # in score points (0..100)
    schedule_loss: float          # in [0, 1]
    rfc_loss: float
    permit_loss: float
    idle_loss: float

    def as_dict(self) -> dict:
        return asdict(self)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _drawing_severity(d: RFCDrawing, *, now=None) -> float:
    now = now or datetime.now(timezone.utc)
    issued = _aware(d.rfc_issued)
    due = _aware(d.rfc_due)
    if issued is not None and issued <= due:
        return 1.0
    ref = issued or now
    days_late = max((ref - due).total_seconds() / 86_400.0, 0.0)
    return max(0.0, 1.0 - days_late / RFC_GRACE_DAYS)


def attribute_for_project(db: Session, project_id: int) -> list[ActivityRiskContribution]:
    project = db.get(Project, project_id)
    if project is None:
        return []

    factors = factors_for(db, project_id)
    rfcs = db.query(RFCDrawing).filter(RFCDrawing.project_id == project_id).all()
    permits = db.query(PermitStatus).filter(PermitStatus.project_id == project_id).all()
    n_rfc = max(len(rfcs), 1)
    n_permit = max(len(permits), 1)

    severity_by_drawing = {d.id: _drawing_severity(d) for d in rfcs}
    granted_by_permit = {p.id: 1.0 if p.status == "granted" else 0.0 for p in permits}

    # Walk active claims to attribute RFC / permit / idle loss to activities.
    contributions: dict[str, dict[str, float]] = {}

    def _bucket(activity_id: str) -> dict[str, float]:
        return contributions.setdefault(
            activity_id,
            {"rfc_loss": 0.0, "permit_loss": 0.0, "idle_loss": 0.0, "sched_loss": 0.0},
        )

    budget = float(project.budget_total or 0)

    for claim in (
        db.query(DelayClaim)
        .filter(DelayClaim.project_id == project_id)
        .all()
    ):
        if not claim.linked_activity_id or claim.status == "rejected":
            continue
        bucket = _bucket(claim.linked_activity_id)

        if claim.rfc_drawing_id is not None:
            sev = severity_by_drawing.get(claim.rfc_drawing_id, 1.0)
            bucket["rfc_loss"] += (1.0 - sev) / n_rfc

        if claim.permit_id is not None:
            granted = granted_by_permit.get(claim.permit_id, 1.0)
            bucket["permit_loss"] += (1.0 - granted) / n_permit

        if claim.idle_event_id is not None and budget > 0:
            evt = db.get(IdleEvent, claim.idle_event_id)
            if evt is not None:
                cost_share = float(evt.computed_cost or 0) / budget
                bucket["idle_loss"] += min(1.0, IDLE_BUDGET_PENALTY_K * cost_share)

    # Schedule loss: split the project-level schedule loss equally across
    # critical-path activities. Non-critical activities contribute zero.
    cpm = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    sched_loss_total = max(0.0, 1.0 - factors.schedule)
    if cpm is not None and cpm.critical_activity_ids and sched_loss_total > 0:
        share = sched_loss_total / max(len(cpm.critical_activity_ids), 1)
        for aid in cpm.critical_activity_ids:
            _bucket(aid)["sched_loss"] = share

    out: list[ActivityRiskContribution] = []
    for aid, parts in contributions.items():
        # Clamp each loss into [0, 1] before weighting so individual
        # decompositions cannot exceed the factor itself.
        loss_sched = min(1.0, max(0.0, parts["sched_loss"]))
        loss_rfc = min(1.0, max(0.0, parts["rfc_loss"]))
        loss_permit = min(1.0, max(0.0, parts["permit_loss"]))
        loss_idle = min(1.0, max(0.0, parts["idle_loss"]))
        impact = 100.0 * (
            WEIGHTS["schedule"] * loss_sched
            + WEIGHTS["rfc"] * loss_rfc
            + WEIGHTS["permit"] * loss_permit
            + WEIGHTS["field_idle"] * loss_idle
        )
        out.append(ActivityRiskContribution(
            activity_id=aid,
            risk_impact=round(impact, 3),
            schedule_loss=round(loss_sched, 4),
            rfc_loss=round(loss_rfc, 4),
            permit_loss=round(loss_permit, 4),
            idle_loss=round(loss_idle, 4),
        ))
    out.sort(key=lambda x: x.risk_impact, reverse=True)
    return out


def attribute_for_activity(
    db: Session, project_id: int, activity_id: str,
) -> ActivityRiskContribution | None:
    for c in attribute_for_project(db, project_id):
        if c.activity_id == activity_id:
            return c
    return None
