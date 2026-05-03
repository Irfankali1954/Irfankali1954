"""CFO Command Center.

The Financial Gatekeeper:

* Owns the per-role visibility policy that drives margin-masking.
* Approves/rejects outbound financial events (delay claims, change orders).
* Reads the project financial summary with margin computed server-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, require_role
from app.core.rbac import (
    FinancialField,
    TechnicalRole,
    VisibilityPolicy,
)
from app.models.financial import (
    CostItem,
    GatekeeperApproval,
    MarginPolicy,
    Project,
    RevenueItem,
)
from app.schemas.financial import (
    GatekeeperDecision,
    ProjectFinancialSummary,
    VisibilityPolicyUpdate,
)
from app.services import cfo_gatekeeper, margin_mask

router = APIRouter()


@router.get("/projects/{project_id}/summary", response_model=ProjectFinancialSummary)
def project_summary(
    project_id: int,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(*list(TechnicalRole))),
) -> ProjectFinancialSummary:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")

    revenue = sum(float(r.amount) for r in db.query(RevenueItem).filter(RevenueItem.project_id == project_id))
    actual_cost = sum(float(c.actual_cost) for c in db.query(CostItem).filter(CostItem.project_id == project_id))
    margin = revenue - actual_cost
    margin_pct = (margin / revenue * 100.0) if revenue else 0.0

    summary = ProjectFinancialSummary(
        project_id=project.id,
        code=project.code,
        revenue=revenue,
        actual_cost=actual_cost,
        margin=margin,
        margin_percent=margin_pct,
        field_idle_cost=0.0,  # joined in by /risk; CFO sees it here
    )
    return margin_mask.apply_visibility(summary, user.role)


@router.get("/visibility-policy")
def read_visibility_policy(
    user: CurrentUser = Depends(require_role(*list(TechnicalRole))),
) -> dict:
    """Return the active per-role allowlist plus catalog metadata.

    Anyone can read the *shape* of the policy (so they know what is
    governable), but only the CFO can ``PUT`` changes.
    """
    pol = margin_mask.get_policy()
    return {
        "fields": [
            {"key": f.value, "label": FinancialField.display(f)}
            for f in FinancialField
        ],
        "roles": [r.value for r in TechnicalRole],
        "policy": {
            r.value: sorted(f.value for f in pol.fields_for(r))
            for r in TechnicalRole
        },
        "viewer_role": user.role.value,
    }


@router.put(
    "/visibility-policy",
    dependencies=[Depends(require_role(TechnicalRole.CFO))],
)
def update_visibility_policy(
    updates: list[VisibilityPolicyUpdate],
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(TechnicalRole.CFO)),
) -> dict:
    """Replace the per-role visibility allowlist. CFO-only."""
    new_allowed: dict[TechnicalRole, frozenset[FinancialField]] = {}
    for u in updates:
        try:
            role = TechnicalRole(u.role)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown role {u.role}")
        new_allowed[role] = frozenset(u.allowed_fields)

        existing = db.query(MarginPolicy).filter(MarginPolicy.role == role.value).one_or_none()
        if existing is None:
            db.add(MarginPolicy(
                role=role.value,
                allowed_fields=[f.value for f in u.allowed_fields],
                updated_by=user.email,
            ))
        else:
            existing.allowed_fields = [f.value for f in u.allowed_fields]
            existing.updated_by = user.email
    db.commit()

    margin_mask.set_policy(VisibilityPolicy(allowed=new_allowed))
    return {"updated_roles": [u.role for u in updates]}


@router.get("/gatekeeper/approvals", dependencies=[Depends(require_role(TechnicalRole.CFO))])
def list_pending(db: Session = Depends(db_session)) -> list[dict]:
    rows = (
        db.query(GatekeeperApproval)
        .filter(GatekeeperApproval.status == "pending")
        .all()
    )
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "subject_type": r.subject_type,
            "subject_id": r.subject_id,
            "amount": float(r.amount),
        }
        for r in rows
    ]


@router.post("/gatekeeper/approvals/{approval_id}", dependencies=[Depends(require_role(TechnicalRole.CFO))])
def decide_approval(
    approval_id: int,
    payload: GatekeeperDecision,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(TechnicalRole.CFO)),
) -> dict:
    try:
        g = cfo_gatekeeper.decide(
            db, approval_id,
            decision=payload.decision,
            cfo_email=user.email,
            notes=payload.notes,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"id": g.id, "status": g.status}
