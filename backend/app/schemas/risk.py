from datetime import datetime
from typing import ClassVar
from pydantic import BaseModel

from app.core.rbac import FinancialField


class RFCDrawingOut(BaseModel):
    id: int
    drawing_no: str
    title: str
    discipline: str
    issuer_org: str
    rfc_due: datetime
    rfc_issued: datetime | None
    overdue_days: float

    model_config = {"from_attributes": True}


class IdleEventOut(BaseModel):
    id: int
    cause: str
    started_at: datetime
    ended_at: datetime | None
    idle_crew: int
    idle_equipment: list[str]
    computed_cost: float | None = None

    model_config = {"from_attributes": True}

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "computed_cost": FinancialField.FIELD_IDLE_COST,
    }


class FieldIdleCostBreakdown(BaseModel):
    project_id: int
    rfc_drawing_no: str | None
    idle_hours: float
    crew_cost: float | None = None
    equipment_cost: float | None = None
    total: float | None = None

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "crew_cost": FinancialField.FIELD_IDLE_COST,
        "equipment_cost": FinancialField.FIELD_IDLE_COST,
        "total": FinancialField.FIELD_IDLE_COST,
    }


class WrapScoreOut(BaseModel):
    project_id: int
    computed_at: datetime
    score: float
    schedule_factor: float
    rfc_factor: float
    permit_factor: float
    long_lead_factor: float
    field_idle_factor: float
    notes: str | None

    model_config = {"from_attributes": True}


class DelayClaimOut(BaseModel):
    id: int
    causing_org: str
    opened_at: datetime
    impact_days: float
    impact_value: float | None = None
    status: str

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "impact_value": FinancialField.DELAY_CLAIM_VALUE,
    }
