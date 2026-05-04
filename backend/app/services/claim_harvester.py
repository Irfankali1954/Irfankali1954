"""Delay-Claim Harvester.

When an :class:`IdleEvent` opens (RFC miss or permit delay), this module:

1. Resolves the *subject artifact* (RFC drawing or permit) the event blames.
2. Pulls every :class:`Message` tagged to that artifact, ordered chronologically.
3. Computes the **CPM Macro-Impact** — days the projected finish has slipped
   versus the contractual COD target — using the latest CPM snapshot.
4. Renders the persisted **Statement of Facts** via
   :func:`app.services.defense_packet.render_statement_of_facts`.
5. Persists a :class:`DelayClaim` (status ``draft``).
6. Opens a pending :class:`GatekeeperApproval` so the CFO sees it in the queue.

This is the "Engineering missed a deliverable → defensible commercial
document" loop, with no human typing required.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.rbac import TechnicalRole
from app.models.financial import Project
from app.models.messaging import Message
from app.models.risk import (
    DelayClaim,
    IdleEvent,
    PermitStatus,
    RFCDrawing,
)
from app.models.schedule import CriticalPathSnapshot
from app.services import cfo_gatekeeper, defense_packet, margin_mask


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _serialize_messages(rows: list[Message]) -> list[dict]:
    out: list[dict] = []
    for m in rows:
        ts = _aware(m.created_at)
        out.append({
            "ts": ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "?",
            "from": m.sender_email,
            "org": m.sender_org,
            "body": m.body,
            "mentions": list(m.mentions or []),
        })
    return out


def _cod_shift_days(db: Session, project: Project) -> tuple[float, datetime | None]:
    """Return (days_slipped, projected_finish) using the latest CPM snapshot."""
    cpm = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project.id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    if cpm is None or project.cod_target is None:
        return 0.0, None
    finish = _aware(cpm.project_finish)
    target = _aware(project.cod_target)
    if finish is None or target is None:
        return 0.0, finish
    delta = (finish - target).total_seconds() / 86_400.0
    return max(0.0, delta), finish


def harvest_for_idle_event(db: Session, idle_event_id: int) -> DelayClaim:
    """Idempotent: re-running on the same idle event returns the existing claim."""
    evt = db.get(IdleEvent, idle_event_id)
    if evt is None:
        raise LookupError(f"idle event {idle_event_id} not found")

    existing = (
        db.query(DelayClaim)
        .filter(DelayClaim.idle_event_id == evt.id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    project = db.get(Project, evt.project_id)
    if project is None:
        raise LookupError(f"project {evt.project_id} not found")

    # --- 1. Resolve subject + causing org ---------------------------------
    rfc: RFCDrawing | None = None
    permit: PermitStatus | None = None
    subject_kind = "rfc"
    subject_ref = ""
    causing_org = "Unknown"

    if evt.rfc_drawing_id is not None:
        rfc = db.get(RFCDrawing, evt.rfc_drawing_id)
        if rfc is not None:
            subject_kind = "rfc"
            subject_ref = rfc.drawing_no
            causing_org = rfc.issuer_org
    elif evt.permit_id is not None:
        permit = db.get(PermitStatus, evt.permit_id)
        if permit is not None:
            subject_kind = "permit"
            subject_ref = permit.permit_type
            causing_org = permit.authority

    # --- 2. Harvest tagged messages ---------------------------------------
    if rfc is not None:
        msg_rows = (
            db.query(Message)
            .filter(Message.rfc_drawing_id == rfc.id)
            .order_by(Message.created_at)
            .all()
        )
    elif permit is not None:
        msg_rows = (
            db.query(Message)
            .filter(Message.permit_id == permit.id)
            .order_by(Message.created_at)
            .all()
        )
    else:
        msg_rows = []
    communications = _serialize_messages(msg_rows)

    # --- 3. CPM macro-impact ---------------------------------------------
    cod_shift, _projected_finish = _cod_shift_days(db, project)

    # --- 4. Build claim row (Statement of Facts rendered post-flush) ------
    started = _aware(evt.started_at) or datetime.now(timezone.utc)
    impact_days = max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 86_400.0)
    impact_value = float(evt.computed_cost or 0)

    claim = DelayClaim(
        project_id=evt.project_id,
        causing_org=causing_org,
        rfc_drawing_id=rfc.id if rfc else None,
        permit_id=permit.id if permit else None,
        idle_event_id=evt.id,
        subject_kind=subject_kind,
        subject_ref=subject_ref,
        communications=communications,
        impact_days=impact_days,
        cod_shift_days=cod_shift,
        impact_value=impact_value,
        status="draft",
    )
    db.add(claim)
    db.flush()  # populate claim.id for the SoF render and approval link

    # --- 5. Render & persist the Statement of Facts -----------------------
    ctx = defense_packet.PacketContext(
        claim=claim,
        project=project,
        idle_event=evt,
        rfc=rfc,
        permit=permit,
        messages=communications,
        viewer_role=TechnicalRole.CFO,         # SoF persisted at full fidelity
        policy=margin_mask.get_policy(),
    )
    claim.statement_of_facts = defense_packet.render_statement_of_facts(ctx)

    # --- 6. Open the CFO approval gate ------------------------------------
    approval = cfo_gatekeeper.open_approval(
        db,
        project_id=evt.project_id,
        subject_type="delay_claim",
        subject_id=claim.id,
        amount=impact_value,
    )
    claim.approval_id = approval.id

    db.commit()
    db.refresh(claim)

    # Silo-buster: alert the right tier the moment a claim is drafted so
    # field idle does not bleed unnoticed.
    try:
        from app.services import notification_service
        notification_service.evaluate_for_project(
            db, evt.project_id,
            trigger="claim_drafted",
            idle_event_id=evt.id,
            claim_id=claim.id,
        )
    except Exception:  # pragma: no cover
        pass

    return claim
