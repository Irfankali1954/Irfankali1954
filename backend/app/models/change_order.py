"""Change Order Sentinel — data model.

The change order is the *single largest profit-eating mechanism* in the EPC
contract. Most of the loss happens not in negotiation but in **time bars**:
the contract gives the contractor N days to give *notice* of a change and
M days to *file the claim*; missing either deadline forfeits the right to
recover. This model encodes the clock from the start.

Status flow::

    pending_notice  ── notice sent ──▶  notice_sent
    notice_sent     ── claim filed ──▶  claim_filed
    claim_filed     ── CFO approves ─▶  approved
    (any stage)     ── rejected/withdrawn/superseded

Notice and claim deadlines are **derived** from
``discovered_at + notice_period_days / claim_period_days``. Both clocks
start the moment the field becomes aware of the potential change — that is
the contractually material event under most master agreements.

Financials are split into ``direct_cost`` (what the work costs the EPC)
and ``markup_value`` (the internal margin we add on top). The CFO is the
only role with the visibility-policy bit to see ``markup_value`` and
``proposed_value``; subcontractors and external engineers see only the
direct cost — *open book on the cost, closed book on the markup*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, JSON, DateTime, Numeric, Float, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ChangeOrder(Base):
    __tablename__ = "change_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    co_number: Mapped[str] = mapped_column(String(64), index=True)        # e.g. CO-014
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(4000), default="")

    originator_org: Mapped[str] = mapped_column(String(128))               # who flagged the change
    originator_email: Mapped[str] = mapped_column(String(255))
    contract_clause: Mapped[str] = mapped_column(String(64), default="")   # e.g. "GC-12.4"
    source: Mapped[str] = mapped_column(String(32), default="manual")      # manual | procore | aconex | bluebeam

    # --- Time-Bar clocks (the heart of the Sentinel) ----------------------
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    notice_period_days: Mapped[int] = mapped_column(Integer, default=7)
    claim_period_days: Mapped[int] = mapped_column(Integer, default=21)
    notice_due_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claim_due_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Gantt linkage ----------------------------------------------------
    linked_activity_id: Mapped[str] = mapped_column(String(64), index=True)
    estimated_duration_impact_days: Mapped[float] = mapped_column(Float, default=0)
    on_critical_path: Mapped[bool] = mapped_column(Boolean, default=False)
    cpm_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Financials (margin-masked at API boundary) -----------------------
    direct_cost: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    markup_pct: Mapped[float] = mapped_column(Float, default=0)
    markup_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    proposed_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    cfo_approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("gatekeeper_approvals.id"), nullable=True,
    )

    status: Mapped[str] = mapped_column(String(32), default="pending_notice", index=True)
    # pending_notice | notice_sent | claim_filed | approved | rejected | withdrawn | superseded

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    events: Mapped[list["ChangeOrderEvent"]] = relationship(
        back_populates="change_order", cascade="all,delete-orphan",
        order_by="ChangeOrderEvent.occurred_at",
    )


class ChangeOrderEvent(Base):
    """Append-only audit row — every status change, notice, claim, comment."""
    __tablename__ = "change_order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_order_id: Mapped[int] = mapped_column(ForeignKey("change_orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_email: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    change_order: Mapped[ChangeOrder] = relationship(back_populates="events")
