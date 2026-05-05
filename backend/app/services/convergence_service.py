"""Convergence of Truth.

Reconciles :class:`DelayClaim` rows against approved :class:`ChangeOrder`
rows so the books never double-count damages that have already been
converted into approved scope.

Net Exposure formula
====================

For any activity::

    net_exposure(activity_id) =
        Σ (gross claim impact for activity)
        − Σ (approved CO recovery for activity)

A negative result clamps to zero (an over-recovery is *not* an EPC asset
to claim against — it is a defended over-payment that gets returned
through normal commercial channels). The reconciler distributes the
recovery proportionally across active claims on the same activity, so
each claim's persisted ``co_offset_value`` reflects its share.

Double-Dip Auditor
==================

When a new claim is harvested for an activity that already has at least
one approved Change Order, ``double_count_flag`` is set on the claim and
a Tier-3 notification is fired so the CFO sees it before the claim is
ever filed. The CFO then makes the call: withdraw the claim, narrow its
scope, or approve the doubling explicitly with counsel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.change_order import ChangeOrder
from app.models.risk import DelayClaim


# Statuses that count toward exposure / recovery.
ACTIVE_CLAIM_STATUSES = {"draft", "approved", "filed"}
APPROVED_CO_STATUSES = {"approved"}


@dataclass
class ActivityExposure:
    project_id: int
    activity_id: str
    gross_claim_impact: float
    approved_co_recovery: float
    net_exposure: float
    claim_ids: list[int]
    change_order_ids: list[int]
    double_count_risk: bool        # at least one claim+CO overlap on this activity
    fully_de_risked: bool          # recovery >= gross claim


@dataclass
class DoubleCountFinding:
    claim_id: int
    activity_id: str
    overlapping_change_order_ids: list[int]
    gross_claim_impact: float
    approved_co_recovery: float


def _active_claims(db: Session, project_id: int, activity_id: str) -> list[DelayClaim]:
    return [
        c for c in db.query(DelayClaim)
        .filter(
            DelayClaim.project_id == project_id,
            DelayClaim.linked_activity_id == activity_id,
        )
        .all()
        if c.status in ACTIVE_CLAIM_STATUSES
    ]


def _approved_cos(db: Session, project_id: int, activity_id: str) -> list[ChangeOrder]:
    return [
        co for co in db.query(ChangeOrder)
        .filter(
            ChangeOrder.project_id == project_id,
            ChangeOrder.linked_activity_id == activity_id,
        )
        .all()
        if co.status in APPROVED_CO_STATUSES
    ]


def compute_for_activity(
    db: Session, project_id: int, activity_id: str,
) -> ActivityExposure:
    """Pure read — does not mutate any rows."""
    claims = _active_claims(db, project_id, activity_id)
    cos = _approved_cos(db, project_id, activity_id)

    gross = sum(float(c.impact_value or 0) for c in claims)
    recovery = sum(float(co.proposed_value or 0) for co in cos)
    net = max(0.0, gross - recovery)
    return ActivityExposure(
        project_id=project_id,
        activity_id=activity_id,
        gross_claim_impact=gross,
        approved_co_recovery=recovery,
        net_exposure=net,
        claim_ids=[c.id for c in claims],
        change_order_ids=[co.id for co in cos],
        double_count_risk=bool(claims) and bool(cos),
        fully_de_risked=bool(claims) and recovery >= gross,
    )


def compute_for_project(db: Session, project_id: int) -> list[ActivityExposure]:
    """One row per activity that carries either a claim or an approved CO."""
    activity_ids: set[str] = set()
    for c in db.query(DelayClaim).filter(DelayClaim.project_id == project_id).all():
        if c.linked_activity_id and c.status in ACTIVE_CLAIM_STATUSES:
            activity_ids.add(c.linked_activity_id)
    for co in db.query(ChangeOrder).filter(ChangeOrder.project_id == project_id).all():
        if co.linked_activity_id and co.status in APPROVED_CO_STATUSES:
            activity_ids.add(co.linked_activity_id)
    return [compute_for_activity(db, project_id, a) for a in sorted(activity_ids)]


def reconcile_activity(
    db: Session, project_id: int, activity_id: str,
) -> ActivityExposure:
    """Mutating: distributes approved-CO recovery across active claims and
    persists each claim's ``co_offset_value``. Idempotent — calling twice
    in a row produces the same offsets."""
    expo = compute_for_activity(db, project_id, activity_id)
    claims = _active_claims(db, project_id, activity_id)
    if not claims:
        return expo
    if expo.gross_claim_impact <= 0:
        # No gross impact means nothing to offset; clear stale offsets.
        for c in claims:
            c.co_offset_value = 0
        db.commit()
        return expo

    recovery = expo.approved_co_recovery
    for c in claims:
        share = float(c.impact_value or 0) / expo.gross_claim_impact
        applied = min(float(c.impact_value or 0), share * recovery)
        c.co_offset_value = applied
    db.commit()
    return compute_for_activity(db, project_id, activity_id)


def reconcile_for_change_order(db: Session, co: ChangeOrder) -> ActivityExposure | None:
    """Hook from the CO approval flow. When a CO is approved, every claim on
    the same activity must be re-reconciled so the CFO Exposure dashboard
    reflects reality before the next bank audit."""
    if co.linked_activity_id is None:
        return None
    return reconcile_activity(db, co.project_id, co.linked_activity_id)


def evaluate_double_count_risk(
    db: Session, claim: DelayClaim,
) -> DoubleCountFinding | None:
    """Set ``claim.double_count_flag`` and return a finding if the claim's
    activity already has approved CO coverage. Caller persists the flag
    via the normal commit path."""
    if not claim.linked_activity_id:
        claim.double_count_flag = False
        return None
    cos = _approved_cos(db, claim.project_id, claim.linked_activity_id)
    if not cos:
        claim.double_count_flag = False
        return None
    claim.double_count_flag = True
    recovery = sum(float(co.proposed_value or 0) for co in cos)
    return DoubleCountFinding(
        claim_id=claim.id,
        activity_id=claim.linked_activity_id,
        overlapping_change_order_ids=[co.id for co in cos],
        gross_claim_impact=float(claim.impact_value or 0),
        approved_co_recovery=recovery,
    )


def fire_double_count_alert(
    db: Session, finding: DoubleCountFinding, project_id: int,
) -> None:
    """Tier-3 alert routed through the existing notification bus.

    A double-count is *guaranteed* nuclear: it happens silently across
    desks (PD drafts a claim, CFO approves a CO; nobody notices the
    overlap until the auditor does). The CEO and CFO get pinged.
    """
    from datetime import timedelta

    from app.models.notification import Notification
    from app.services.notification_service import (
        Tier, _fanout, _recently_fired,
    )

    hour_bucket = int(datetime.now(timezone.utc).timestamp() // 3600)
    key = f"double_count:{finding.claim_id}:{finding.activity_id}:{hour_bucket}"
    if _recently_fired(db, key, within=timedelta(hours=1)):
        return

    cos_str = ", ".join(f"#{i}" for i in finding.overlapping_change_order_ids)
    subject = (
        f"[NUCLEAR · DOUBLE-COUNT] Claim #{finding.claim_id} overlaps "
        f"approved CO {cos_str} on activity {finding.activity_id}"
    )
    body = (
        f"Project: {project_id}\n"
        f"Activity: {finding.activity_id}\n"
        f"Claim id: {finding.claim_id}\n"
        f"Gross claim impact: ${finding.gross_claim_impact:,.0f}\n"
        f"Approved CO recovery on same activity: "
        f"${finding.approved_co_recovery:,.0f}\n"
        f"Overlapping COs: {cos_str}\n"
        "\n** POTENTIAL DOUBLE-COUNT **\n"
        "An open Delay Claim has been drafted for an activity that already "
        "carries an approved Change Order. Bank auditors and project "
        "close-out reviewers will read this as the EPC double-counting "
        "damages. Confirm with counsel before filing.\n"
    )
    notif = Notification(
        project_id=project_id,
        tier=Tier.NUCLEAR.value,
        trigger="convergence:double_count",
        subject=subject,
        body=body,
        cpm_drift_days=0.0,
        open_idle_cost=0.0,
        idle_event_id=None,
        claim_id=finding.claim_id,
        dedupe_key=key,
    )
    db.add(notif)
    db.flush()
    notif.dispatched_to = _fanout(db, notif, Tier.NUCLEAR)
    db.commit()
