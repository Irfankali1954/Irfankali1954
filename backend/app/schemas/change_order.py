from datetime import datetime
from typing import ClassVar
from pydantic import BaseModel, EmailStr, Field

from app.core.rbac import FinancialField


# --- Inbound ----------------------------------------------------------------

class ChangeOrderDraftIn(BaseModel):
    project_id: int
    co_number: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    originator_org: str = Field(..., max_length=128)
    contract_clause: str = ""
    linked_activity_id: str = Field(..., min_length=1, max_length=64)
    estimated_duration_impact_days: float = 0.0
    direct_cost: float = 0.0

    notice_period_days: int = 7
    claim_period_days: int = 21
    discovered_at: datetime | None = None
    source: str = "manual"


class MarkupIn(BaseModel):
    """CFO-only — sets the internal markup applied to a CO's direct cost."""
    markup_pct: float = Field(..., ge=0, le=100)


class NoticeSendIn(BaseModel):
    counterparty_email: EmailStr | None = None
    body_override: str | None = None


class ClaimFileIn(BaseModel):
    cover_note: str | None = None


class ProcoreCOIngestRow(BaseModel):
    """Inbound row from a Procore push. Field names mirror the Procore
    ``change_events`` payload so internal dev teams can map 1:1."""
    project_id: int
    co_number: str
    title: str
    description: str = ""
    originator_org: str
    originator_email: EmailStr | None = None
    linked_activity_id: str | None = None
    estimated_duration_impact_days: float | None = None
    direct_cost: float | None = None
    discovered_at: datetime | None = None
    contract_clause: str | None = None


# --- Outbound ---------------------------------------------------------------

class ChangeOrderOut(BaseModel):
    id: int
    project_id: int
    co_number: str
    title: str
    description: str
    originator_org: str
    originator_email: str
    contract_clause: str
    source: str

    discovered_at: datetime
    notice_period_days: int
    claim_period_days: int
    notice_due_by: datetime
    claim_due_by: datetime
    notice_sent_at: datetime | None
    claim_filed_at: datetime | None

    linked_activity_id: str
    estimated_duration_impact_days: float
    on_critical_path: bool
    cpm_assessed_at: datetime | None

    direct_cost: float | None = None
    markup_pct: float | None = None
    markup_value: float | None = None
    proposed_value: float | None = None
    cfo_approval_id: int | None

    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "direct_cost": FinancialField.CHANGE_ORDER_DIRECT_COST,
        "markup_pct": FinancialField.CHANGE_ORDER_MARKUP,
        "markup_value": FinancialField.CHANGE_ORDER_MARKUP,
        "proposed_value": FinancialField.CHANGE_ORDER_TOTAL,
    }


class ChangeOrderEventOut(BaseModel):
    id: int
    event_type: str
    actor_email: str
    payload: dict
    occurred_at: datetime

    model_config = {"from_attributes": True}


class AgingItem(BaseModel):
    change_order_id: int
    co_number: str
    title: str
    status: str
    deadline: datetime
    deadline_kind: str           # "notice" | "claim"
    seconds_remaining: float
    on_critical_path: bool
    severity: str                # "ok" | "approaching" | "missed"


class SentinelReport(BaseModel):
    project_id: int
    evaluated_at: datetime
    items: list[AgingItem]
    notifications_fired: int
