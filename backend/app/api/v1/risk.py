"""Master-Wrap Risk Engine endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, require_permission
from app.core.rbac import Permission
from app.models.risk import IdleEvent, RFCDrawing, WrapScoreSnapshot
from app.schemas.risk import (
    FieldIdleCostBreakdown,
    IdleEventOut,
    RFCDrawingOut,
    WrapScoreOut,
)
from app.services import field_idle_cost, margin_mask, wrap_risk

router = APIRouter()


@router.get(
    "/projects/{project_id}/wrap-score",
    response_model=WrapScoreOut,
    dependencies=[Depends(require_permission(Permission.RISK_READ))],
)
def latest_wrap_score(project_id: int, db: Session = Depends(db_session)) -> WrapScoreOut:
    snap = (
        db.query(WrapScoreSnapshot)
        .filter(WrapScoreSnapshot.project_id == project_id)
        .order_by(WrapScoreSnapshot.computed_at.desc())
        .first()
    )
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no wrap score yet")
    return WrapScoreOut.model_validate(snap)


@router.post(
    "/projects/{project_id}/wrap-score/recompute",
    response_model=WrapScoreOut,
    dependencies=[Depends(require_permission(Permission.RISK_RECALC))],
)
def recompute_wrap_score(project_id: int, db: Session = Depends(db_session)) -> WrapScoreOut:
    snap = wrap_risk.compute(db, project_id)
    return WrapScoreOut.model_validate(snap)


@router.get(
    "/projects/{project_id}/rfc-misses",
    response_model=list[RFCDrawingOut],
    dependencies=[Depends(require_permission(Permission.RISK_READ))],
)
def rfc_misses(project_id: int, db: Session = Depends(db_session)) -> list[RFCDrawingOut]:
    now = datetime.now(timezone.utc)
    rows = (
        db.query(RFCDrawing)
        .filter(RFCDrawing.project_id == project_id)
        .all()
    )
    out: list[RFCDrawingOut] = []
    for r in rows:
        if r.rfc_issued is not None and r.rfc_issued <= r.rfc_due:
            continue
        ref = r.rfc_issued or now
        overdue = max((ref - r.rfc_due).total_seconds() / 86_400.0, 0.0)
        out.append(RFCDrawingOut(
            id=r.id, drawing_no=r.drawing_no, title=r.title,
            discipline=r.discipline, issuer_org=r.issuer_org,
            rfc_due=r.rfc_due, rfc_issued=r.rfc_issued, overdue_days=overdue,
        ))
    return out


@router.get(
    "/idle-events",
    response_model=list[IdleEventOut],
    dependencies=[Depends(require_permission(Permission.RISK_READ))],
)
def list_idle_events(
    project_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.RISK_READ)),
) -> list[IdleEventOut]:
    rows = db.query(IdleEvent).filter(IdleEvent.project_id == project_id).all()
    out = [IdleEventOut.model_validate(r, from_attributes=True) for r in rows]
    return margin_mask.apply_many(out, user.role)


@router.post(
    "/field-idle-cost",
    response_model=FieldIdleCostBreakdown,
    dependencies=[Depends(require_permission(Permission.RISK_READ))],
)
def compute_field_idle(
    project_id: int,
    rfc_drawing_id: int,
    idle_crew: int,
    crew_burdened_rate: float,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.RISK_READ)),
) -> FieldIdleCostBreakdown:
    rfc = db.get(RFCDrawing, rfc_drawing_id)
    if rfc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rfc drawing not found")
    cost = field_idle_cost.compute_idle_cost(
        rfc_due=rfc.rfc_due,
        idle_crew=idle_crew,
        crew_burdened_rate=crew_burdened_rate,
        equipment_rates=[],
    )
    out = FieldIdleCostBreakdown(
        project_id=project_id,
        rfc_drawing_no=rfc.drawing_no,
        idle_hours=cost.idle_hours,
        crew_cost=cost.crew_cost,
        equipment_cost=cost.equipment_cost,
        total=cost.total,
    )
    return margin_mask.apply_visibility(out, user.role)
