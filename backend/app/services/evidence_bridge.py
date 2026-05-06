"""Evidence Bridge — the "Black Box" attached to every heatmap cell.

For a given (project_id, activity_id) returns three things the CEO/CFO
need when they click a cell from the heatmap:

1. **Communication Logs** — every Message tagged to the activity itself,
   to any RFC drawing referenced by an active claim on the activity, or
   to any permit referenced. Ordered chronologically.
2. **Hashed Audit Trail** — one row per persisted artifact (claim, CO,
   idle event, CO event) with a SHA-256 hash of canonical fields, so the
   recipient can prove the row has not been tampered with after the fact.
3. **Subcontractor Scorecard** — per-org performance roll-up across the
   activity's evidence: RFC on-time %, claim count, gross/net claim
   value, CO count, CO approval rate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.change_order import ChangeOrder, ChangeOrderEvent
from app.models.messaging import Message
from app.models.risk import (
    DelayClaim,
    IdleEvent,
    PermitStatus,
    RFCDrawing,
)


@dataclass
class CommunicationRow:
    timestamp: str
    sender_email: str
    sender_org: str
    body: str
    activity_id: str | None
    rfc_drawing_id: int | None
    permit_id: int | None
    mentions: list[str]


@dataclass
class AuditRow:
    kind: str            # claim | change_order | idle_event | co_event
    id: int
    occurred_at: str
    actor: str | None
    canonical: str
    sha256: str


@dataclass
class ScorecardRow:
    org: str
    role: str            # issuer | counterparty | originator
    rfc_total: int
    rfc_on_time: int
    rfc_on_time_pct: float
    claim_count: int
    claim_gross_total: float
    claim_net_total: float
    co_count: int
    co_approved: int
    co_approval_pct: float
    double_count_flagged: int


@dataclass
class EvidenceBundle:
    project_id: int
    activity_id: str
    generated_at: str
    communications: list[CommunicationRow]
    audit_trail: list[AuditRow]
    scorecard: list[ScorecardRow]
    bundle_hash: str     # SHA-256 over the audit trail row hashes


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt(dt: datetime | None) -> str:
    a = _aware(dt)
    return a.strftime("%Y-%m-%d %H:%M UTC") if a else "n/a"


def _canonical(*parts: Any) -> str:
    """Deterministic string form for hashing — ``|`` separated, str-coerced."""
    return "|".join("" if p is None else str(p) for p in parts)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _audit_row(kind: str, *, row_id: int, when: datetime | None,
               actor: str | None, canonical: str) -> AuditRow:
    return AuditRow(
        kind=kind, id=row_id,
        occurred_at=_fmt(when),
        actor=actor,
        canonical=canonical,
        sha256=_sha256(canonical),
    )


def build(db: Session, *, project_id: int, activity_id: str) -> EvidenceBundle:
    # --- 1. Pull all artifacts on this activity --------------------------
    claims = (
        db.query(DelayClaim)
        .filter(
            DelayClaim.project_id == project_id,
            DelayClaim.linked_activity_id == activity_id,
        )
        .all()
    )
    rfc_ids = {c.rfc_drawing_id for c in claims if c.rfc_drawing_id is not None}
    permit_ids = {c.permit_id for c in claims if c.permit_id is not None}
    idle_ids = {c.idle_event_id for c in claims if c.idle_event_id is not None}

    cos = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.project_id == project_id,
            ChangeOrder.linked_activity_id == activity_id,
        )
        .all()
    )

    rfcs = list(db.query(RFCDrawing).filter(RFCDrawing.id.in_(rfc_ids)).all()) if rfc_ids else []
    permits = list(db.query(PermitStatus).filter(PermitStatus.id.in_(permit_ids)).all()) if permit_ids else []
    idle_events = list(db.query(IdleEvent).filter(IdleEvent.id.in_(idle_ids)).all()) if idle_ids else []
    co_events = (
        db.query(ChangeOrderEvent)
        .filter(ChangeOrderEvent.change_order_id.in_([co.id for co in cos]))
        .order_by(ChangeOrderEvent.occurred_at)
        .all()
        if cos else []
    )

    # --- 2. Communication Logs (Black Box) -------------------------------
    msg_filters = [Message.activity_id == activity_id]
    if rfc_ids:
        msg_filters.append(Message.rfc_drawing_id.in_(rfc_ids))
    if permit_ids:
        msg_filters.append(Message.permit_id.in_(permit_ids))
    messages = (
        db.query(Message)
        .filter(or_(*msg_filters))
        .order_by(Message.created_at)
        .all()
    )
    communications = [
        CommunicationRow(
            timestamp=_fmt(m.created_at),
            sender_email=m.sender_email,
            sender_org=m.sender_org,
            body=m.body,
            activity_id=m.activity_id,
            rfc_drawing_id=m.rfc_drawing_id,
            permit_id=m.permit_id,
            mentions=list(m.mentions or []),
        )
        for m in messages
    ]

    # --- 3. Hashed Audit Trail -------------------------------------------
    audit: list[AuditRow] = []
    for c in claims:
        audit.append(_audit_row(
            "claim", row_id=c.id, when=c.opened_at, actor=c.causing_org,
            canonical=_canonical(
                "claim", c.id, c.causing_org, c.subject_kind, c.subject_ref,
                c.linked_activity_id, c.impact_value, c.co_offset_value,
                c.status, c.double_count_flag, c.opened_at,
            ),
        ))
    for co in cos:
        audit.append(_audit_row(
            "change_order", row_id=co.id, when=co.created_at, actor=co.originator_email,
            canonical=_canonical(
                "co", co.id, co.co_number, co.linked_activity_id,
                co.direct_cost, co.markup_pct, co.proposed_value,
                co.status, co.notice_sent_at, co.claim_filed_at,
            ),
        ))
    for e in idle_events:
        audit.append(_audit_row(
            "idle_event", row_id=e.id, when=e.started_at, actor=None,
            canonical=_canonical(
                "idle", e.id, e.cause, e.idle_crew, e.computed_cost,
                e.rfc_drawing_id, e.permit_id, e.started_at, e.ended_at,
            ),
        ))
    for ev in co_events:
        audit.append(_audit_row(
            "co_event", row_id=ev.id, when=ev.occurred_at, actor=ev.actor_email,
            canonical=_canonical(
                "co_event", ev.id, ev.change_order_id, ev.event_type,
                ev.actor_email, ev.occurred_at,
            ),
        ))
    audit.sort(key=lambda r: r.occurred_at)
    bundle_hash = _sha256("\n".join(r.sha256 for r in audit)) if audit else _sha256("")

    # --- 4. Subcontractor Scorecard --------------------------------------
    org_index: dict[str, dict] = {}

    def _bucket(org: str, role: str) -> dict:
        key = f"{org}::{role}"
        return org_index.setdefault(
            key,
            {
                "org": org, "role": role,
                "rfc_total": 0, "rfc_on_time": 0,
                "claim_count": 0, "claim_gross_total": 0.0, "claim_net_total": 0.0,
                "co_count": 0, "co_approved": 0,
                "double_count_flagged": 0,
            },
        )

    now = datetime.now(timezone.utc)
    for d in rfcs:
        b = _bucket(d.issuer_org, "issuer")
        b["rfc_total"] += 1
        issued = _aware(d.rfc_issued)
        due = _aware(d.rfc_due)
        if issued is not None and due is not None and issued <= due:
            b["rfc_on_time"] += 1

    for c in claims:
        b = _bucket(c.causing_org or "Unknown", "counterparty")
        b["claim_count"] += 1
        gross = float(c.impact_value or 0)
        offset = float(c.co_offset_value or 0)
        b["claim_gross_total"] += gross
        b["claim_net_total"] += max(0.0, gross - offset)
        if c.double_count_flag:
            b["double_count_flagged"] += 1

    for co in cos:
        b = _bucket(co.originator_org, "originator")
        b["co_count"] += 1
        if co.status == "approved":
            b["co_approved"] += 1

    scorecard: list[ScorecardRow] = []
    for entry in org_index.values():
        rfc_pct = (
            (entry["rfc_on_time"] / entry["rfc_total"] * 100.0)
            if entry["rfc_total"] else 0.0
        )
        co_pct = (
            (entry["co_approved"] / entry["co_count"] * 100.0)
            if entry["co_count"] else 0.0
        )
        scorecard.append(ScorecardRow(
            org=entry["org"], role=entry["role"],
            rfc_total=entry["rfc_total"], rfc_on_time=entry["rfc_on_time"],
            rfc_on_time_pct=round(rfc_pct, 1),
            claim_count=entry["claim_count"],
            claim_gross_total=round(entry["claim_gross_total"], 2),
            claim_net_total=round(entry["claim_net_total"], 2),
            co_count=entry["co_count"], co_approved=entry["co_approved"],
            co_approval_pct=round(co_pct, 1),
            double_count_flagged=entry["double_count_flagged"],
        ))
    scorecard.sort(key=lambda s: (-s.claim_gross_total, s.org))

    return EvidenceBundle(
        project_id=project_id,
        activity_id=activity_id,
        generated_at=_fmt(now),
        communications=communications,
        audit_trail=audit,
        scorecard=scorecard,
        bundle_hash=bundle_hash,
    )
