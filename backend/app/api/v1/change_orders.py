"""Change Order Sentinel API.

* POST /change-orders                       — draft a CO (any drafter)
* GET  /change-orders?project_id=…          — list (margin-masked)
* GET  /change-orders/{id}                  — detail (margin-masked)
* POST /change-orders/{id}/notice           — record formal notice sent
* POST /change-orders/{id}/file-claim       — record claim filed
* PUT  /change-orders/{id}/markup           — CFO-only markup setter
* POST /change-orders/{id}/approve          — CFO-only final approval
* POST /change-orders/from-procore          — accretive ingest from Procore
* GET  /change-orders/aging?project_id=…    — sentinel snapshot for dashboard
* POST /change-orders/sentinel/scan         — manual sentinel run
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, get_current_user, require_permission
from app.core.rbac import Permission
from app.models.change_order import ChangeOrder
from app.models.financial import GatekeeperApproval
from app.models.schedule import ScheduleActivity
from app.schemas.change_order import (
    AgingItem,
    ChangeOrderDraftIn,
    ChangeOrderEventOut,
    ChangeOrderOut,
    ClaimFileIn,
    MarkupIn,
    NoticeSendIn,
    ProcoreCOIngestRow,
    SentinelReport,
)
from app.schemas.ingest import IngestHealthReportOut
from app.services import (
    cfo_gatekeeper,
    change_order_sentinel,
    ingest_validation,
    margin_mask,
)

router = APIRouter()


def _verify_activity(db: Session, project_id: int, activity_id: str) -> None:
    a = (
        db.query(ScheduleActivity)
        .filter(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.activity_id == activity_id,
        )
        .first()
    )
    if a is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"linked_activity_id '{activity_id}' not found on project {project_id}",
        )


def _to_out(co: ChangeOrder, viewer: CurrentUser) -> ChangeOrderOut:
    out = ChangeOrderOut.model_validate(co, from_attributes=True)
    return margin_mask.apply_visibility(out, viewer.role)


@router.post(
    "",
    response_model=ChangeOrderOut,
    summary="Draft a Change Order (clock starts)",
    description=(
        "Creates a CO in ``pending_notice`` state. The Sentinel begins "
        "tracking the contract notice/claim deadlines from "
        "``discovered_at`` (defaults to now). The linked activity is "
        "validated against the Gantt; a CO must always have one."
    ),
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_DRAFT))],
)
def draft(
    payload: ChangeOrderDraftIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_DRAFT)),
) -> ChangeOrderOut:
    _verify_activity(db, payload.project_id, payload.linked_activity_id)
    co = ChangeOrder(
        project_id=payload.project_id,
        co_number=payload.co_number,
        title=payload.title,
        description=payload.description,
        originator_org=payload.originator_org,
        originator_email=user.email,
        contract_clause=payload.contract_clause,
        source=payload.source,
        discovered_at=payload.discovered_at or datetime.now(timezone.utc),
        notice_period_days=payload.notice_period_days,
        claim_period_days=payload.claim_period_days,
        linked_activity_id=payload.linked_activity_id,
        estimated_duration_impact_days=payload.estimated_duration_impact_days,
        direct_cost=payload.direct_cost,
    )
    change_order_sentinel.compute_deadlines(co)
    db.add(co)
    db.flush()
    change_order_sentinel.assess_critical_path(db, co)
    change_order_sentinel.record_event(
        db, co, event_type="drafted", actor_email=user.email,
        payload={"source": payload.source},
    )
    db.commit()
    db.refresh(co)
    return _to_out(co, user)


@router.get("", response_model=list[ChangeOrderOut])
def list_cos(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> list[ChangeOrderOut]:
    rows = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.discovered_at.desc())
        .all()
    )
    return [_to_out(r, user) for r in rows]


@router.get("/aging", response_model=SentinelReport)
def aging_snapshot(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> SentinelReport:
    """Read-only sentinel snapshot for the dashboard. Does NOT fire alerts."""
    items, _ = change_order_sentinel.scan(
        db, project_id, fire_notifications=False, trigger="snapshot",
    )
    return SentinelReport(
        project_id=project_id,
        evaluated_at=datetime.now(timezone.utc),
        items=items,
        notifications_fired=0,
    )


@router.post(
    "/sentinel/scan",
    response_model=SentinelReport,
    dependencies=[Depends(require_permission(Permission.RISK_RECALC))],
)
def sentinel_scan(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
) -> SentinelReport:
    items, fired = change_order_sentinel.scan(db, project_id, trigger="manual")
    return SentinelReport(
        project_id=project_id,
        evaluated_at=datetime.now(timezone.utc),
        items=items,
        notifications_fired=fired,
    )


@router.get("/{co_id}", response_model=ChangeOrderOut)
def get_co(
    co_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    return _to_out(co, user)


@router.get("/{co_id}/events", response_model=list[ChangeOrderEventOut])
def get_events(
    co_id: int,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[ChangeOrderEventOut]:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    return [ChangeOrderEventOut.model_validate(e) for e in co.events]


@router.post(
    "/{co_id}/notice",
    response_model=ChangeOrderOut,
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND))],
)
def send_notice(
    co_id: int,
    payload: NoticeSendIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    try:
        change_order_sentinel.send_notice(
            db, co, actor_email=user.email,
            payload={"counterparty_email": payload.counterparty_email},
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    db.commit()
    db.refresh(co)
    return _to_out(co, user)


@router.post(
    "/{co_id}/file-claim",
    response_model=ChangeOrderOut,
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND))],
)
def file_claim(
    co_id: int,
    payload: ClaimFileIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    try:
        change_order_sentinel.file_claim(
            db, co, actor_email=user.email, payload={"cover_note": payload.cover_note},
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

    # Open the CFO approval gate the moment a claim is filed — the CFO sets
    # markup *and* approves before the proposal goes to the counterparty.
    if co.cfo_approval_id is None:
        approval = cfo_gatekeeper.open_approval(
            db, project_id=co.project_id,
            subject_type="change_order", subject_id=co.id,
            amount=float(co.proposed_value or co.direct_cost or 0),
        )
        co.cfo_approval_id = approval.id

    db.commit()
    db.refresh(co)
    return _to_out(co, user)


@router.put(
    "/{co_id}/markup",
    response_model=ChangeOrderOut,
    summary="Set the internal markup on a Change Order (CFO only)",
    description=(
        "Closed-book to subcontractors, external engineers, and even the "
        "Admin. The Visibility Policy decides who can read the resulting "
        "``markup_pct`` / ``markup_value`` / ``proposed_value`` — by "
        "default only the CFO can see them after this call lands."
    ),
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_MARKUP_WRITE))],
)
def set_markup(
    co_id: int,
    payload: MarkupIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_MARKUP_WRITE)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    change_order_sentinel.apply_markup(
        db, co, markup_pct=payload.markup_pct, actor_email=user.email,
    )

    # If a CFO approval gate exists, sync its amount with the new total.
    if co.cfo_approval_id:
        approval = db.get(GatekeeperApproval, co.cfo_approval_id)
        if approval and approval.status == "pending":
            approval.amount = float(co.proposed_value or 0)

    db.commit()
    db.refresh(co)
    return _to_out(co, user)


@router.post(
    "/{co_id}/approve",
    response_model=ChangeOrderOut,
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_APPROVE))],
)
def approve(
    co_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_APPROVE)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    if co.cfo_approval_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no approval gate has been opened — file the claim first")
    approval = db.get(GatekeeperApproval, co.cfo_approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "approval row missing")

    cfo_gatekeeper.decide(
        db, approval.id, decision="approve",
        cfo_email=user.email, notes="CO approved via /change-orders/{id}/approve",
    )
    co.status = "approved"
    change_order_sentinel.record_event(
        db, co, event_type="approved", actor_email=user.email,
        payload={"proposed_value": float(co.proposed_value or 0)},
    )
    db.commit()

    # Convergence of Truth — the moment a CO is approved, every active
    # claim on the same activity must be re-reconciled so the books
    # reflect the real net exposure for the next bank audit.
    try:
        from app.services import convergence_service
        convergence_service.reconcile_for_change_order(db, co)
    except Exception:  # pragma: no cover
        pass

    db.refresh(co)
    return _to_out(co, user)


@router.post(
    "/{co_id}/reject",
    response_model=ChangeOrderOut,
    summary="Reject a Change Order (CFO only) — releases any prior offset",
    description=(
        "Flips the linked GatekeeperApproval to ``rejected`` and the CO to "
        "``rejected``, then re-runs the Convergence reconcile so any "
        "DelayClaim that was previously offset by this CO has its "
        "``co_offset_value`` released. The Statement of Facts on those "
        "claims is **not** auto-rewritten — the CFO does that explicitly "
        "via the Convergence dashboard so the audit trail is deliberate."
    ),
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_APPROVE))],
)
def reject(
    co_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_APPROVE)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    if co.cfo_approval_id is not None:
        approval = db.get(GatekeeperApproval, co.cfo_approval_id)
        if approval and approval.status == "pending":
            cfo_gatekeeper.decide(
                db, approval.id, decision="reject",
                cfo_email=user.email, notes="CO rejected via /change-orders/{id}/reject",
            )
    co.status = "rejected"
    change_order_sentinel.record_event(
        db, co, event_type="rejected", actor_email=user.email,
    )
    db.commit()

    # Reverse de-risk — once this CO is no longer ``approved`` it must stop
    # offsetting any DelayClaim on the same activity.
    try:
        from app.services import convergence_service
        convergence_service.reconcile_for_change_order(db, co)
    except Exception:  # pragma: no cover
        pass

    db.refresh(co)
    return _to_out(co, user)


@router.post(
    "/{co_id}/withdraw",
    response_model=ChangeOrderOut,
    summary="Withdraw a Change Order — also releases any prior offset",
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND))],
)
def withdraw(
    co_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_NOTICE_SEND)),
) -> ChangeOrderOut:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change order not found")
    co.status = "withdrawn"
    change_order_sentinel.record_event(
        db, co, event_type="withdrawn", actor_email=user.email,
    )
    db.commit()

    try:
        from app.services import convergence_service
        convergence_service.reconcile_for_change_order(db, co)
    except Exception:  # pragma: no cover
        pass

    db.refresh(co)
    return _to_out(co, user)


@router.post(
    "/from-procore",
    summary="Bulk push Change Events from Procore",
    description=(
        "Internal EPC dev teams use this endpoint to forward field-flagged "
        "change events directly from Procore. The response includes an "
        "``ingest_health`` block — internal devs should aim for grade A "
        "by populating ``linked_activity_id``, ``direct_cost``, and "
        "``discovered_at`` on every row."
    ),
    dependencies=[Depends(require_permission(Permission.CHANGE_ORDER_DRAFT))],
)
def from_procore(
    rows: list[ProcoreCOIngestRow],
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.CHANGE_ORDER_DRAFT)),
) -> dict:
    expectations = [
        ingest_validation.FieldExpectation("project_id", True, 0.0, "project FK"),
        ingest_validation.FieldExpectation("co_number", True, 0.0, "CO number"),
        ingest_validation.FieldExpectation("title", True, 0.0, "Title"),
        ingest_validation.FieldExpectation("originator_org", True, 0.0, "Originator"),
        ingest_validation.FieldExpectation("linked_activity_id", False, 25.0,
                                           "Gantt linkage — required for CPM impact"),
        ingest_validation.FieldExpectation("direct_cost", False, 15.0,
                                           "Direct cost — required for financial damages"),
        ingest_validation.FieldExpectation("discovered_at", False, 10.0,
                                           "Discovery time — anchors the time-bar clock"),
        ingest_validation.FieldExpectation("contract_clause", False, 5.0,
                                           "Citing contract clause supports defensibility"),
    ]
    health = ingest_validation.evaluate(
        source="procore:change_events",
        records=[r.model_dump() for r in rows],
        expectations=expectations,
    )

    created: list[int] = []
    for r in rows:
        if not r.linked_activity_id:
            continue  # cannot create a CO without a Gantt link
        try:
            _verify_activity(db, r.project_id, r.linked_activity_id)
        except HTTPException:
            continue
        co = ChangeOrder(
            project_id=r.project_id,
            co_number=r.co_number,
            title=r.title,
            description=r.description,
            originator_org=r.originator_org,
            originator_email=str(r.originator_email or user.email),
            contract_clause=r.contract_clause or "",
            source="procore",
            discovered_at=r.discovered_at or datetime.now(timezone.utc),
            linked_activity_id=r.linked_activity_id,
            estimated_duration_impact_days=r.estimated_duration_impact_days or 0,
            direct_cost=r.direct_cost or 0,
        )
        change_order_sentinel.compute_deadlines(co)
        db.add(co)
        db.flush()
        change_order_sentinel.assess_critical_path(db, co)
        change_order_sentinel.record_event(
            db, co, event_type="drafted", actor_email=user.email,
            payload={"source": "procore"},
        )
        created.append(co.id)
    db.commit()

    return {
        "created_change_order_ids": created,
        "ingest_health": IngestHealthReportOut(**{**health.__dict__, "grade": health.grade}).model_dump(),
    }
