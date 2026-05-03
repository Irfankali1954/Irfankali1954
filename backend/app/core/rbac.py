"""Federated RBAC.

Two independent axes of authorization:

* **Technical role** — managed by the *Admin*. Controls which API surfaces a
  user can reach (e.g. can they invoke the ERP sync endpoint, can they import
  an XER file, can they create users).
* **Visibility policy** — managed by the *CFO*. Controls which financial
  fields a user is permitted to *see* in any payload that crosses the API
  boundary (margin, unit cost, supplier rate, etc.).

Endpoints check technical role via :func:`require_role`. Schemas apply the
visibility policy via :mod:`app.services.margin_mask`.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field


class TechnicalRole(str, Enum):
    ADMIN = "admin"                 # platform owner — manages users + tech
    CFO = "cfo"                     # financial gatekeeper — manages visibility
    PROJECT_DIRECTOR = "project_director"
    EPC_MANAGER = "epc_manager"
    SITE_MANAGER = "site_manager"
    CIVIL_ENGINEER = "civil_engineer"
    SUBCONTRACTOR = "subcontractor"
    SUPPLIER = "supplier"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Tech permissions (Admin-managed)
    USERS_MANAGE = "users:manage"
    ERP_SYNC = "erp:sync"
    SCHEDULE_IMPORT = "schedule:import"
    SCHEDULE_WRITE = "schedule:write"
    DAILY_LOG_SUBMIT = "daily_log:submit"
    RISK_READ = "risk:read"
    RISK_RECALC = "risk:recalc"
    EDC_DRAWING_UPLOAD = "edc:drawing:upload"
    DELAY_CLAIM_FILE = "delay_claim:file"

    # CFO-only tech permissions
    FINANCIAL_GATEKEEPER = "financial:gatekeeper"
    VISIBILITY_POLICY_WRITE = "visibility_policy:write"


# Default tech-permission matrix. Admin can edit at runtime via /admin/roles.
DEFAULT_PERMISSIONS: dict[TechnicalRole, set[Permission]] = {
    TechnicalRole.ADMIN: set(Permission),
    TechnicalRole.CFO: {
        Permission.FINANCIAL_GATEKEEPER,
        Permission.VISIBILITY_POLICY_WRITE,
        Permission.RISK_READ,
        Permission.RISK_RECALC,
    },
    TechnicalRole.PROJECT_DIRECTOR: {
        Permission.RISK_READ,
        Permission.RISK_RECALC,
        Permission.SCHEDULE_IMPORT,
        Permission.SCHEDULE_WRITE,
        Permission.DELAY_CLAIM_FILE,
    },
    TechnicalRole.EPC_MANAGER: {
        Permission.SCHEDULE_WRITE,
        Permission.RISK_READ,
        Permission.DAILY_LOG_SUBMIT,
        Permission.DELAY_CLAIM_FILE,
    },
    TechnicalRole.SITE_MANAGER: {
        Permission.DAILY_LOG_SUBMIT,
        Permission.SCHEDULE_WRITE,
        Permission.RISK_READ,
    },
    TechnicalRole.CIVIL_ENGINEER: {
        Permission.EDC_DRAWING_UPLOAD,
        Permission.DAILY_LOG_SUBMIT,
        Permission.RISK_READ,
    },
    TechnicalRole.SUBCONTRACTOR: {
        Permission.DAILY_LOG_SUBMIT,
    },
    TechnicalRole.SUPPLIER: {
        Permission.DAILY_LOG_SUBMIT,
    },
    TechnicalRole.VIEWER: set(),
}


# --- Visibility (CFO-managed) ----------------------------------------------

class FinancialField(str, Enum):
    """Financial fields that may be masked from any serialized payload."""
    BUDGET_TOTAL = "budget_total"
    ACTUAL_COST = "actual_cost"
    UNIT_COST = "unit_cost"
    SUPPLIER_RATE = "supplier_rate"
    MARGIN = "margin"
    MARGIN_PERCENT = "margin_percent"
    REVENUE = "revenue"
    FIELD_IDLE_COST = "field_idle_cost"
    DELAY_CLAIM_VALUE = "delay_claim_value"


@dataclass(frozen=True)
class VisibilityPolicy:
    """Per-role allowlist of financial fields.

    The CFO is the only role that may write this policy. The Admin cannot.
    Anything not in the allowlist is masked at the schema boundary.
    """
    allowed: dict[TechnicalRole, frozenset[FinancialField]] = field(default_factory=dict)

    def fields_for(self, role: TechnicalRole) -> frozenset[FinancialField]:
        return self.allowed.get(role, frozenset())


def default_visibility_policy() -> VisibilityPolicy:
    """Conservative default. CFO sees all; nobody else sees margin."""
    all_fields = frozenset(FinancialField)
    cost_only = frozenset({
        FinancialField.BUDGET_TOTAL,
        FinancialField.ACTUAL_COST,
        FinancialField.FIELD_IDLE_COST,
    })
    return VisibilityPolicy(
        allowed={
            TechnicalRole.ADMIN: frozenset(),  # Admin sees tech, not money
            TechnicalRole.CFO: all_fields,
            TechnicalRole.PROJECT_DIRECTOR: all_fields - {FinancialField.MARGIN, FinancialField.MARGIN_PERCENT},
            TechnicalRole.EPC_MANAGER: cost_only,
            TechnicalRole.SITE_MANAGER: frozenset({FinancialField.FIELD_IDLE_COST}),
            TechnicalRole.CIVIL_ENGINEER: frozenset(),
            TechnicalRole.SUBCONTRACTOR: frozenset(),
            TechnicalRole.SUPPLIER: frozenset(),
            TechnicalRole.VIEWER: frozenset(),
        }
    )
