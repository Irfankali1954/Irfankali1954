"""Delay-Claim API.

* GET  /claims                          — list claims (project-filtered)
* GET  /claims/{id}                     — single claim, financial fields masked
* GET  /claims/{id}/packet?format=md|html
* POST /claims/by-idle-event/{idle_id}  — explicit harvest (idempotent)
* POST /claims/{id}/finalize            — gated on CFO approval being 'approved'

The CFO uses the existing approval workflow at
``POST /api/v1/cfo/gatekeeper/approvals/{approval_id}`` to approve or reject;
this router consults that decision before allowing finalization.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, get_current_user, require_permission
from app.core.rbac import Permission, TechnicalRole
from app.models.financial import GatekeeperApproval, Project
from app.models.risk import DelayClaim, IdleEvent, PermitStatus, RFCDrawing
from app.schemas.claims import DelayClaimOut
from app.services import claim_harvester, defense_packet, margin_mask

router = APIRouter()


def _to_out(row: DelayClaim, viewer: CurrentUser) -> DelayClaimOut:
    out = DelayClaimOut.model_validate(row, from_attributes=True)
    return margin_mask.apply_visibility(out, viewer.role)


def _packet_context(db: Session, claim: DelayClaim, viewer: CurrentUser) -> defense_packet.PacketContext:
    project = db.get(Project, claim.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    evt = db.get(IdleEvent, claim.idle_event_id) if claim.idle_event_id else None
    if evt is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "claim is missing its idle event")
    rfc = db.get(RFCDrawing, claim.rfc_drawing_id) if claim.rfc_drawing_id else None
    permit = db.get(PermitStatus, claim.permit_id) if claim.permit_id else None
    return defense_packet.PacketContext(
        claim=claim, project=project, idle_event=evt,
        rfc=rfc, permit=permit,
        messages=list(claim.communications or []),
        viewer_role=viewer.role,
        policy=margin_mask.get_policy(),
    )


@router.get("", response_model=list[DelayClaimOut])
def list_claims(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> list[DelayClaimOut]:
    rows = (
        db.query(DelayClaim)
        .filter(DelayClaim.project_id == project_id)
        .order_by(DelayClaim.opened_at.desc())
        .all()
    )
    return [_to_out(r, user) for r in rows]


@router.get("/{claim_id}", response_model=DelayClaimOut)
def get_claim(
    claim_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> DelayClaimOut:
    claim = db.get(DelayClaim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    return _to_out(claim, user)


@router.post(
    "/by-idle-event/{idle_event_id}",
    response_model=DelayClaimOut,
    dependencies=[Depends(require_permission(Permission.DELAY_CLAIM_FILE))],
)
def harvest(
    idle_event_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.DELAY_CLAIM_FILE)),
) -> DelayClaimOut:
    try:
        claim = claim_harvester.harvest_for_idle_event(db, idle_event_id)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return _to_out(claim, user)


@router.get("/{claim_id}/packet")
def get_packet(
    claim_id: int,
    format: str = Query("md", pattern="^(md|html|pdf|docx)$"),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Render the Commercial Defense Packet.

    ``md`` and ``html`` are fully implemented. ``pdf`` and ``docx`` are
    stubs that return 501 — wire `reportlab` / `python-docx` once legal
    has finalized the corporate letterhead template.
    """
    claim = db.get(DelayClaim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    ctx = _packet_context(db, claim, user)

    if format == "md":
        return PlainTextResponse(
            defense_packet.render_packet_markdown(ctx),
            media_type="text/markdown; charset=utf-8",
        )
    if format == "html":
        return HTMLResponse(defense_packet.render_packet_html(ctx))
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        f"format={format} requires reportlab/python-docx wiring (TODO)",
    )


@router.post(
    "/{claim_id}/finalize",
    response_model=DelayClaimOut,
    dependencies=[Depends(require_permission(Permission.DELAY_CLAIM_FILE))],
)
def finalize(
    claim_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.DELAY_CLAIM_FILE)),
) -> DelayClaimOut:
    """Move a claim ``draft → filed`` only after CFO approval.

    The approval check is the *teeth* of the gate: a Project Director with
    ``DELAY_CLAIM_FILE`` permission still cannot file a claim until the CFO
    (or, by future delegation, the PD acting under CFO authority) flips the
    linked :class:`GatekeeperApproval` to ``approved``.
    """
    claim = db.get(DelayClaim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    if claim.status == "filed":
        return _to_out(claim, user)
    if claim.approval_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "claim has no approval gate")
    approval = db.get(GatekeeperApproval, claim.approval_id)
    if approval is None or approval.status != "approved":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"approval gate blocks finalization (status={approval.status if approval else 'missing'})",
        )

    claim.status = "filed"
    claim.finalized_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(claim)
    return _to_out(claim, user)
