"""CFO Command Center.

The Financial Gatekeeper:

* Owns the per-role visibility policy that drives margin-masking.
* Approves/rejects outbound financial events (delay claims, change orders).
* Reads the project financial summary with margin computed server-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.services import cfo_gatekeeper, convergence_service, margin_mask

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


@router.get(
    "/net-exposure",
    summary="Net Exposure dashboard (Convergence of Truth)",
    description=(
        "Returns one row per activity that carries either an active Delay "
        "Claim or an approved Change Order, plus a project-level total. "
        "Net exposure = Σ gross claim impact − Σ approved CO recovery, "
        "clamped at 0. Financial figures are masked per the CFO Visibility "
        "Policy: callers without ``DELAY_CLAIM_VALUE`` see ``null`` for "
        "the dollar fields and only the structural shape (claim ids, CO "
        "ids, double-count flags) of the exposure."
    ),
)
def net_exposure(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(*list(TechnicalRole))),
) -> dict:
    rows = convergence_service.compute_for_project(db, project_id)
    allowed = margin_mask.get_policy().fields_for(user.role)
    show_money = FinancialField.DELAY_CLAIM_VALUE in allowed

    def _m(v: float) -> float | None:
        return v if show_money else None

    items = [
        {
            "activity_id": r.activity_id,
            "gross_claim_impact": _m(r.gross_claim_impact),
            "approved_co_recovery": _m(r.approved_co_recovery),
            "net_exposure": _m(r.net_exposure),
            "claim_ids": r.claim_ids,
            "change_order_ids": r.change_order_ids,
            "double_count_risk": r.double_count_risk,
            "fully_de_risked": r.fully_de_risked,
        }
        for r in rows
    ]
    totals = {
        "gross_claim_impact": _m(sum(r.gross_claim_impact for r in rows)),
        "approved_co_recovery": _m(sum(r.approved_co_recovery for r in rows)),
        "net_exposure": _m(sum(r.net_exposure for r in rows)),
        "double_count_activities": sum(1 for r in rows if r.double_count_risk),
    }
    return {
        "project_id": project_id,
        "items": items,
        "totals": totals,
        "viewer_role": user.role.value,
        "money_visible": show_money,
    }


@router.post(
    "/net-exposure/reconcile",
    dependencies=[Depends(require_role(TechnicalRole.CFO))],
)
def reconcile_all(
    project_id: int = Query(...),
    db: Session = Depends(db_session),
) -> dict:
    """CFO-only manual reconcile across every activity on the project.

    Idempotent — re-runs the offset distribution for every claim/CO pair
    so the books are fresh before a bank audit.
    """
    rows = convergence_service.compute_for_project(db, project_id)
    for r in rows:
        convergence_service.reconcile_activity(db, project_id, r.activity_id)
    return {"project_id": project_id, "activities_reconciled": len(rows)}


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
