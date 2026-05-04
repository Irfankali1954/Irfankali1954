"""Master-Wrap Risk Engine endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, require_permission
from app.core.rbac import Permission
from app.models.financial import CostItem, Project, RevenueItem
from app.models.risk import IdleEvent, PermitStatus, RFCDrawing, WrapScoreSnapshot
from app.models.schedule import ScheduleActivity
from app.schemas.risk import (
    FieldIdleCostBreakdown,
    IdleEventOut,
    RFCDrawingOut,
    WrapScoreOut,
)
from app.services import field_idle_cost, margin_mask, wrap_risk

router = APIRouter()


class SimulateRfcMissIn(BaseModel):
    rfc_drawing_id: int
    days_overdue: int = 7
    idle_crew: int = 12
    crew_burdened_rate: float = 120.0


class SimulatePermitDelayIn(BaseModel):
    permit_id: int
    days_overdue: int = 7
    idle_crew: int = 18
    crew_burdened_rate: float = 130.0


class FactorsOut(BaseModel):
    schedule: float
    rfc: float
    permit: float
    long_lead: float
    field_idle: float


class SimulationOut(BaseModel):
    before_score: float
    after_score: float
    delta: float
    idle_cost: float | None  # masked
    factors_before: FactorsOut
    factors_after: FactorsOut
    claim_id: int | None = None
    approval_id: int | None = None


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


@router.post(
    "/projects/{project_id}/seed-demo",
    dependencies=[Depends(require_permission(Permission.RISK_RECALC))],
)
def seed_demo(project_id: int, db: Session = Depends(db_session)) -> dict:
    """Idempotent demo seed so the Simulate Delay button has data to act on.

    Creates a project, two RFC drawings, two cost items, two activities.
    Real data wires in via :mod:`app.api.v1.erp` once Oracle/P6 are live.
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    project = db.get(Project, project_id)
    if project is None:
        project = Project(
            id=project_id,
            code=f"DEMO-{project_id}",
            name="Demo Combined-Cycle Plant",
            cod_target=now + timedelta(days=540),
            contract_type="EPC_LSTK",
            contract_value=1_200_000_000,
            budget_total=1_050_000_000,
        )
        db.add(project)
        db.flush()

    if db.query(RFCDrawing).filter(RFCDrawing.project_id == project_id).count() == 0:
        db.add_all([
            RFCDrawing(
                project_id=project_id, drawing_no="X-102",
                title="HRSG Foundation Reinforcement Plan",
                discipline="civil", issuer_org="ExternalCivilCo",
                rfc_due=now + timedelta(days=14),
            ),
            RFCDrawing(
                project_id=project_id, drawing_no="E-310",
                title="Switchyard Earthing Layout",
                discipline="elec", issuer_org="ExternalElecCo",
                rfc_due=now + timedelta(days=21),
            ),
        ])

    if db.query(PermitStatus).filter(PermitStatus.project_id == project_id).count() == 0:
        db.add_all([
            PermitStatus(
                project_id=project_id,
                permit_type="Air Quality Construction Permit",
                authority="State EPA Region 7",
                target_date=now + timedelta(days=10),
                status="pending",
            ),
            PermitStatus(
                project_id=project_id,
                permit_type="Wetlands 404 Permit",
                authority="USACE District",
                target_date=now + timedelta(days=18),
                status="pending",
            ),
        ])

    if db.query(ScheduleActivity).filter(ScheduleActivity.project_id == project_id).count() == 0:
        db.add_all([
            ScheduleActivity(
                project_id=project_id, activity_id="CIV-1040",
                name="HRSG Foundation Pour", wbs="1.2.3",
                planned_start=now + timedelta(days=20),
                planned_finish=now + timedelta(days=35),
                duration_days=15, predecessors=[], successors=["MEC-2010"],
            ),
            ScheduleActivity(
                project_id=project_id, activity_id="MEC-2010",
                name="HRSG Erection", wbs="2.1.1",
                planned_start=now + timedelta(days=36),
                planned_finish=now + timedelta(days=120),
                duration_days=84, predecessors=["CIV-1040"], successors=[],
            ),
        ])

    if db.query(CostItem).filter(CostItem.project_id == project_id).count() == 0:
        db.add_all([
            CostItem(project_id=project_id, wbs="1.2.3", description="Concrete & rebar",
                     supplier="LocalConcreteCo", quantity=4500, unit_cost=180,
                     actual_cost=810_000, supplier_rate=180),
            CostItem(project_id=project_id, wbs="2.1.1", description="HRSG package",
                     supplier="LongLeadVendor", quantity=1, unit_cost=120_000_000,
                     actual_cost=0, supplier_rate=0),
        ])
    if db.query(RevenueItem).filter(RevenueItem.project_id == project_id).count() == 0:
        db.add_all([
            RevenueItem(project_id=project_id, milestone="Civil Substantial Completion",
                        amount=300_000_000),
            RevenueItem(project_id=project_id, milestone="HRSG Mechanical Completion",
                        amount=600_000_000),
        ])
    db.commit()

    rfcs = db.query(RFCDrawing).filter(RFCDrawing.project_id == project_id).all()
    permits = db.query(PermitStatus).filter(PermitStatus.project_id == project_id).all()
    return {
        "project_id": project_id,
        "rfc_drawings": [{"id": r.id, "drawing_no": r.drawing_no, "rfc_due": r.rfc_due.isoformat()} for r in rfcs],
        "permits": [{"id": p.id, "permit_type": p.permit_type, "authority": p.authority,
                     "target_date": p.target_date.isoformat(), "status": p.status} for p in permits],
    }


@router.post(
    "/projects/{project_id}/simulate-rfc-miss",
    response_model=SimulationOut,
    dependencies=[Depends(require_permission(Permission.RISK_RECALC))],
)
def simulate_rfc_miss(
    project_id: int,
    payload: SimulateRfcMissIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.RISK_RECALC)),
) -> SimulationOut:
    """Synthetic RFC miss → IdleEvent → wrap score recompute.

    The UI's *Simulate Delay* button hits this. The result lets the user
    see the wrap score swing before any real ERP/EDC data is wired up.
    Field idle cost is masked at the boundary per the CFO's policy.
    """
    try:
        sim = wrap_risk.simulate_rfc_miss(
            db,
            project_id=project_id,
            rfc_drawing_id=payload.rfc_drawing_id,
            days_overdue=payload.days_overdue,
            idle_crew=payload.idle_crew,
            crew_burdened_rate=payload.crew_burdened_rate,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rfc drawing not found")

    out = SimulationOut(
        before_score=sim.before_score,
        after_score=sim.after_score,
        delta=sim.delta,
        idle_cost=sim.idle_cost,
        factors_before=FactorsOut(**sim.factors_before.__dict__),
        factors_after=FactorsOut(**sim.factors_after.__dict__),
        claim_id=sim.claim_id,
        approval_id=sim.approval_id,
    )
    # mask field-idle cost per CFO policy
    from app.core.rbac import FinancialField
    allowed = margin_mask.get_policy().fields_for(user.role)
    if FinancialField.FIELD_IDLE_COST not in allowed:
        out = out.model_copy(update={"idle_cost": None})
    return out


@router.post(
    "/projects/{project_id}/simulate-permit-delay",
    response_model=SimulationOut,
    dependencies=[Depends(require_permission(Permission.RISK_RECALC))],
)
def simulate_permit_delay(
    project_id: int,
    payload: SimulatePermitDelayIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.RISK_RECALC)),
) -> SimulationOut:
    """Permit-delay sibling of :func:`simulate_rfc_miss`. Backdates a permit
    target date, opens an :class:`IdleEvent` linked via ``permit_id``, and
    auto-drafts a Delay Claim so the permit-delay path has full parity."""
    try:
        sim = wrap_risk.simulate_permit_delay(
            db,
            project_id=project_id,
            permit_id=payload.permit_id,
            days_overdue=payload.days_overdue,
            idle_crew=payload.idle_crew,
            crew_burdened_rate=payload.crew_burdened_rate,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "permit not found")

    out = SimulationOut(
        before_score=sim.before_score,
        after_score=sim.after_score,
        delta=sim.delta,
        idle_cost=sim.idle_cost,
        factors_before=FactorsOut(**sim.factors_before.__dict__),
        factors_after=FactorsOut(**sim.factors_after.__dict__),
        claim_id=sim.claim_id,
        approval_id=sim.approval_id,
    )
    from app.core.rbac import FinancialField
    allowed = margin_mask.get_policy().fields_for(user.role)
    if FinancialField.FIELD_IDLE_COST not in allowed:
        out = out.model_copy(update={"idle_cost": None})
    return out


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
