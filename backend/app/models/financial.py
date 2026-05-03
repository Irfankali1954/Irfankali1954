"""Financial data model.

All money-bearing fields live here. They are masked at the schema layer
according to the CFO-managed visibility policy — never trust the UI.
"""

from datetime import datetime, timezone
from sqlalchemy import String, Numeric, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    cod_target: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    contract_type: Mapped[str] = mapped_column(String(32), default="EPC_LSTK")  # LSTK, cost-plus, etc.

    # Money. Masked at the boundary.
    contract_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    budget_total: Mapped[float] = mapped_column(Numeric(18, 2), default=0)

    cost_items: Mapped[list["CostItem"]] = relationship(back_populates="project", cascade="all,delete-orphan")
    revenue_items: Mapped[list["RevenueItem"]] = relationship(back_populates="project", cascade="all,delete-orphan")


class CostItem(Base):
    __tablename__ = "cost_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    wbs: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    supplier: Mapped[str | None] = mapped_column(String(128), nullable=True)

    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # masked
    actual_cost: Mapped[float] = mapped_column(Numeric(18, 2), default=0)  # masked
    supplier_rate: Mapped[float] = mapped_column(Numeric(18, 4), default=0)  # masked

    project: Mapped[Project] = relationship(back_populates="cost_items")


class RevenueItem(Base):
    __tablename__ = "revenue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)  # masked
    recognized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="revenue_items")


class MarginPolicy(Base):
    """The CFO's per-role visibility decisions, persisted.

    Loaded at startup into :class:`app.core.rbac.VisibilityPolicy`. Editable
    only via :func:`app.api.v1.cfo.update_visibility_policy`.
    """
    __tablename__ = "margin_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    allowed_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class GatekeeperApproval(Base):
    """CFO sign-off on outbound financial events (claims, change orders, etc.)."""
    __tablename__ = "gatekeeper_approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(64))  # e.g. "delay_claim", "change_order"
    subject_id: Mapped[int] = mapped_column()
    amount: Mapped[float] = mapped_column(Numeric(18, 2))  # masked
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|approved|rejected
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
