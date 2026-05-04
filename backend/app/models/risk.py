from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime, Numeric, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RFCDrawing(Base):
    """An engineering drawing tracked through to 'Released for Construction'."""
    __tablename__ = "rfc_drawings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    drawing_no: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    discipline: Mapped[str] = mapped_column(String(32))  # civil, mech, elec, instr
    issuer_org: Mapped[str] = mapped_column(String(128))

    rfc_due: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rfc_issued: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edc_source: Mapped[str] = mapped_column(String(32), default="procore")  # procore|aconex|bluebeam
    edc_doc_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PermitStatus(Base):
    __tablename__ = "permit_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    permit_type: Mapped[str] = mapped_column(String(64))
    authority: Mapped[str] = mapped_column(String(128))
    target_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|granted|rejected


class IdleEvent(Base):
    """A measured period when crew/equipment was idle waiting on a missing input."""
    __tablename__ = "idle_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    rfc_drawing_id: Mapped[int | None] = mapped_column(ForeignKey("rfc_drawings.id"), nullable=True)
    permit_id: Mapped[int | None] = mapped_column(ForeignKey("permit_status.id"), nullable=True)
    cause: Mapped[str] = mapped_column(String(64))  # missing_rfc | missing_permit | long_lead

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    idle_crew: Mapped[int] = mapped_column(default=0)
    idle_equipment: Mapped[list[str]] = mapped_column(JSON, default=list)
    crew_burdened_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # masked
    equipment_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)      # masked
    computed_cost: Mapped[float] = mapped_column(Numeric(18, 2), default=0)        # masked


class WrapScoreSnapshot(Base):
    """The ‘Wrap Risk Score’ — likelihood of hitting COD, recomputed on cadence."""
    __tablename__ = "wrap_score_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    score: Mapped[float] = mapped_column(Float)  # 0..100, P(hit COD)
    schedule_factor: Mapped[float] = mapped_column(Float)
    rfc_factor: Mapped[float] = mapped_column(Float)
    permit_factor: Mapped[float] = mapped_column(Float)
    long_lead_factor: Mapped[float] = mapped_column(Float)
    field_idle_factor: Mapped[float] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class DelayClaim(Base):
    """Auto-built delay-claim packet for 'wrap responsibility' management.

    A claim is *harvested* the moment an :class:`IdleEvent` opens — the agent
    pulls all context-tagged :class:`Message`\\ s into ``communications``,
    renders the Statement of Facts, computes COD slip, and parks the
    resulting record at the CFO Approval Gate. Status flow:

        ``draft`` → CFO approves → ``approved`` → user finalizes → ``filed``
                  ↘ CFO rejects → ``rejected``
    """
    __tablename__ = "delay_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    causing_org: Mapped[str] = mapped_column(String(128))
    rfc_drawing_id: Mapped[int | None] = mapped_column(ForeignKey("rfc_drawings.id"), nullable=True)
    permit_id: Mapped[int | None] = mapped_column(ForeignKey("permit_status.id"), nullable=True)
    idle_event_id: Mapped[int | None] = mapped_column(ForeignKey("idle_events.id"), nullable=True, index=True)

    subject_kind: Mapped[str] = mapped_column(String(16), default="rfc")  # rfc | permit
    subject_ref: Mapped[str] = mapped_column(String(128), default="")     # e.g. drawing_no

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    communications: Mapped[list[dict]] = mapped_column(JSON, default=list)
    impact_days: Mapped[float] = mapped_column(Float, default=0)
    cod_shift_days: Mapped[float] = mapped_column(Float, default=0)
    impact_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)  # masked

    statement_of_facts: Mapped[str | None] = mapped_column(String(16000), nullable=True)
    approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("gatekeeper_approvals.id"), nullable=True,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|approved|rejected|filed
