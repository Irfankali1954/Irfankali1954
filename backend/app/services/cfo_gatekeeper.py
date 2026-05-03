"""CFO Financial Gatekeeper.

Outbound financial events (delay claims, change orders, supplier
re-quotations) require explicit CFO sign-off before they propagate. The
gatekeeper intercepts them, persists a :class:`GatekeeperApproval`, and only
releases the event once the CFO's decision is recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.financial import GatekeeperApproval


def open_approval(
    db: Session,
    *,
    project_id: int,
    subject_type: str,
    subject_id: int,
    amount: float,
) -> GatekeeperApproval:
    g = GatekeeperApproval(
        project_id=project_id,
        subject_type=subject_type,
        subject_id=subject_id,
        amount=amount,
        status="pending",
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def decide(
    db: Session,
    approval_id: int,
    *,
    decision: str,
    cfo_email: str,
    notes: str | None = None,
) -> GatekeeperApproval:
    if decision not in {"approve", "reject"}:
        raise ValueError(f"unknown decision: {decision}")
    g = db.get(GatekeeperApproval, approval_id)
    if g is None:
        raise LookupError("approval not found")
    g.status = "approved" if decision == "approve" else "rejected"
    g.decided_by = cfo_email
    g.decided_at = datetime.now(timezone.utc)
    g.notes = notes
    db.commit()
    db.refresh(g)
    return g
