from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel

from app.core.rbac import FinancialField


class CommunicationOut(BaseModel):
    ts: str
    from_: str
    org: str
    body: str
    mentions: list[str] = []

    model_config = {"populate_by_name": True}


class DelayClaimOut(BaseModel):
    id: int
    project_id: int
    causing_org: str
    subject_kind: str
    subject_ref: str
    rfc_drawing_id: int | None
    permit_id: int | None
    idle_event_id: int | None
    linked_activity_id: str | None
    opened_at: datetime
    impact_days: float
    cod_shift_days: float
    impact_value: float | None = None      # gross, masked
    co_offset_value: float | None = None   # CO recovery applied, masked
    net_impact_value: float | None = None  # impact - offset, masked
    double_count_flag: bool = False
    statement_of_facts: str | None
    approval_id: int | None
    status: str
    finalized_at: datetime | None
    communications: list[dict]

    model_config = {"from_attributes": True}

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "impact_value": FinancialField.DELAY_CLAIM_VALUE,
        "co_offset_value": FinancialField.DELAY_CLAIM_VALUE,
        "net_impact_value": FinancialField.DELAY_CLAIM_VALUE,
    }


class FinalizeIn(BaseModel):
    notes: str | None = None


PacketFormat = Literal["md", "html"]
