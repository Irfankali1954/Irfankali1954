from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class NotificationOut(BaseModel):
    id: int
    project_id: int
    tier: str
    trigger: str
    subject: str
    body: str
    cpm_drift_days: float
    open_idle_cost: float
    idle_event_id: int | None
    claim_id: int | None
    dispatched_to: list[dict]
    created_at: datetime

    model_config = {"from_attributes": True}


class RecipientIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    role_label: str = Field(..., max_length=64)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=32)
    tiers: list[str] = []
    active: bool = True


class RecipientOut(RecipientIn):
    id: int
    updated_by: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvaluateIn(BaseModel):
    project_id: int
    trigger: str = "manual"
