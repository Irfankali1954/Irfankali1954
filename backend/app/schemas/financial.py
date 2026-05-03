"""Financial schemas. All money fields are Optional[float] so the
margin-masking layer can null them out per the CFO's visibility policy.
"""

from datetime import datetime
from typing import ClassVar
from pydantic import BaseModel

from app.core.rbac import FinancialField


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    cod_target: datetime
    contract_type: str
    contract_value: float | None = None
    budget_total: float | None = None

    model_config = {"from_attributes": True}

    # Mapping from API field name → FinancialField key, for the masker.
    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "contract_value": FinancialField.REVENUE,
        "budget_total": FinancialField.BUDGET_TOTAL,
    }


class CostItemOut(BaseModel):
    id: int
    wbs: str
    description: str
    supplier: str | None = None
    quantity: float
    unit_cost: float | None = None
    actual_cost: float | None = None
    supplier_rate: float | None = None

    model_config = {"from_attributes": True}

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "unit_cost": FinancialField.UNIT_COST,
        "actual_cost": FinancialField.ACTUAL_COST,
        "supplier_rate": FinancialField.SUPPLIER_RATE,
    }


class ProjectFinancialSummary(BaseModel):
    """CFO Command Center top-level card. Margin computed server-side."""
    project_id: int
    code: str
    revenue: float | None = None
    actual_cost: float | None = None
    margin: float | None = None
    margin_percent: float | None = None
    field_idle_cost: float | None = None

    MASK_FIELDS: ClassVar[dict[str, FinancialField]] = {
        "revenue": FinancialField.REVENUE,
        "actual_cost": FinancialField.ACTUAL_COST,
        "margin": FinancialField.MARGIN,
        "margin_percent": FinancialField.MARGIN_PERCENT,
        "field_idle_cost": FinancialField.FIELD_IDLE_COST,
    }


class VisibilityPolicyUpdate(BaseModel):
    """CFO-only payload to set per-role allowed financial fields."""
    role: str
    allowed_fields: list[FinancialField]


class GatekeeperDecision(BaseModel):
    decision: str  # "approve" | "reject"
    notes: str | None = None
