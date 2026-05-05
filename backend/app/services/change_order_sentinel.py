"""Change Order Sentinel.

Two responsibilities:

1. **Time-bar clock.** For every active CO, compute time remaining against
   the contractually derived ``notice_due_by`` and ``claim_due_by``
   timestamps. Bucket each CO into:

   * ``ok``           — > 24 h to notice deadline (or > 72 h to claim)
   * ``approaching``  — within the buffer; aging without notice/claim
   * ``missed``       — deadline passed without notice/claim ⇒ TIME BAR

2. **CPM linkage.** Re-assess whether the CO's linked activity is on the
   current critical path so the dashboard can flag changes that move COD.

Notification fan-out is delegated to :mod:`app.services.notification_service`,
which already owns dedupe, recipient resolution, and SMS/email adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.change_order import ChangeOrder, ChangeOrderEvent
from app.models.schedule import CriticalPathSnapshot
from app.schemas.change_order import AgingItem


APPROACHING_NOTICE_WINDOW = timedelta(hours=24)
APPROACHING_CLAIM_WINDOW = timedelta(hours=72)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_deadlines(co: ChangeOrder) -> None:
    """Refresh ``notice_due_by`` / ``claim_due_by`` from ``discovered_at``."""
    discovered = _aware(co.discovered_at) or datetime.now(timezone.utc)
    co.discovered_at = discovered
    co.notice_due_by = discovered + timedelta(days=co.notice_period_days)
    co.claim_due_by = discovered + timedelta(days=co.claim_period_days)


def assess_critical_path(db: Session, co: ChangeOrder) -> bool:
    """Mark whether the CO's linked activity sits on the latest critical path."""
    snap = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == co.project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    on_cp = bool(snap and co.linked_activity_id in (snap.critical_activity_ids or []))
    co.on_critical_path = on_cp
    co.cpm_assessed_at = datetime.now(timezone.utc)
    return on_cp


# ---------------------------------------------------------------------------
# Aging classifier
# ---------------------------------------------------------------------------


@dataclass
class AgingFinding:
    co: ChangeOrder
    deadline_kind: str           # "notice" | "claim"
    deadline: datetime
    seconds_remaining: float
    severity: str                # "ok" | "approaching" | "missed"


def classify(co: ChangeOrder, *, now: datetime | None = None) -> AgingFinding | None:
    """Return the *most pressing* unresolved deadline for this CO, or None
    if the CO is already approved/rejected/withdrawn/superseded."""
    if co.status in {"approved", "rejected", "withdrawn", "superseded"}:
        return None
    now = now or datetime.now(timezone.utc)

    # Notice clock active until the notice has been sent.
    if co.notice_sent_at is None:
        deadline = _aware(co.notice_due_by) or now
        remaining = (deadline - now).total_seconds()
        if remaining <= 0:
            severity = "missed"
        elif remaining <= APPROACHING_NOTICE_WINDOW.total_seconds():
            severity = "approaching"
        else:
            severity = "ok"
        return AgingFinding(
            co=co, deadline_kind="notice", deadline=deadline,
            seconds_remaining=remaining, severity=severity,
        )

    # Notice sent — claim clock now active.
    if co.claim_filed_at is None:
        deadline = _aware(co.claim_due_by) or now
        remaining = (deadline - now).total_seconds()
        if remaining <= 0:
            severity = "missed"
        elif remaining <= APPROACHING_CLAIM_WINDOW.total_seconds():
            severity = "approaching"
        else:
            severity = "ok"
        return AgingFinding(
            co=co, deadline_kind="claim", deadline=deadline,
            seconds_remaining=remaining, severity=severity,
        )
    return None


# ---------------------------------------------------------------------------
# Sentinel scan
# ---------------------------------------------------------------------------


def scan(
    db: Session,
    project_id: int,
    *,
    fire_notifications: bool = True,
    trigger: str = "sentinel",
) -> tuple[list[AgingItem], int]:
    """Walk every active CO, classify it, and (by default) ping the bus.

    Returns ``(report items, notifications_fired)``. ``report items`` always
    contains *every* active CO so the dashboard can render an "all clear"
    view, not just the ones that triggered alerts.
    """
    from app.services import notification_service  # avoid circular import at module load

    rows = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.discovered_at)
        .all()
    )
    now = datetime.now(timezone.utc)
    items: list[AgingItem] = []
    fired = 0

    for co in rows:
        # Re-check CP linkage every scan (cheap relative to the alert cost).
        assess_critical_path(db, co)
        finding = classify(co, now=now)
        if finding is None:
            continue

        items.append(AgingItem(
            change_order_id=co.id,
            co_number=co.co_number,
            title=co.title,
            status=co.status,
            deadline=finding.deadline,
            deadline_kind=finding.deadline_kind,
            seconds_remaining=finding.seconds_remaining,
            on_critical_path=co.on_critical_path,
            severity=finding.severity,
        ))

        if not fire_notifications or finding.severity == "ok":
            continue

        notif = notification_service.evaluate_change_order_alert(
            db,
            change_order_id=co.id,
            deadline_kind=finding.deadline_kind,
            severity=finding.severity,
            seconds_remaining=finding.seconds_remaining,
            on_critical_path=co.on_critical_path,
            trigger=trigger,
        )
        if notif is not None:
            fired += 1

    db.commit()
    return items, fired


# ---------------------------------------------------------------------------
# Lifecycle helpers (used by the API router)
# ---------------------------------------------------------------------------


def record_event(
    db: Session,
    co: ChangeOrder,
    *,
    event_type: str,
    actor_email: str,
    payload: dict | None = None,
) -> ChangeOrderEvent:
    evt = ChangeOrderEvent(
        change_order_id=co.id,
        event_type=event_type,
        actor_email=actor_email,
        payload=payload or {},
    )
    db.add(evt)
    co.updated_at = datetime.now(timezone.utc)
    return evt


def send_notice(
    db: Session, co: ChangeOrder, *, actor_email: str, payload: dict | None = None,
) -> ChangeOrder:
    if co.status != "pending_notice":
        raise ValueError(f"cannot send notice from status={co.status}")
    co.notice_sent_at = datetime.now(timezone.utc)
    co.status = "notice_sent"
    record_event(db, co, event_type="notice_sent", actor_email=actor_email, payload=payload)
    return co


def file_claim(
    db: Session, co: ChangeOrder, *, actor_email: str, payload: dict | None = None,
) -> ChangeOrder:
    if co.status != "notice_sent":
        raise ValueError(f"cannot file claim from status={co.status}")
    co.claim_filed_at = datetime.now(timezone.utc)
    co.status = "claim_filed"
    record_event(db, co, event_type="claim_filed", actor_email=actor_email, payload=payload)
    return co


def apply_markup(
    db: Session, co: ChangeOrder, *, markup_pct: float, actor_email: str,
) -> ChangeOrder:
    """CFO-only path. Recomputes ``markup_value`` and ``proposed_value``."""
    co.markup_pct = float(markup_pct)
    co.markup_value = float(co.direct_cost or 0) * (markup_pct / 100.0)
    co.proposed_value = float(co.direct_cost or 0) + co.markup_value
    record_event(
        db, co, event_type="markup_applied", actor_email=actor_email,
        payload={"markup_pct": markup_pct},
    )
    return co
