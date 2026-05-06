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
from app.services import (
    cfo_gatekeeper, convergence_service, evidence_bridge, margin_mask,
    risk_attribution, risk_heatmap,
)

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
    attribs = {a.activity_id: a for a in risk_attribution.attribute_for_project(db, project_id)}
    allowed = margin_mask.get_policy().fields_for(user.role)
    show_money = FinancialField.DELAY_CLAIM_VALUE in allowed

    def _m(v: float) -> float | None:
        return v if show_money else None

    items = []
    for r in rows:
        attr = attribs.get(r.activity_id)
        items.append({
            "activity_id": r.activity_id,
            "gross_claim_impact": _m(r.gross_claim_impact),
            "approved_co_recovery": _m(r.approved_co_recovery),
            "net_exposure": _m(r.net_exposure),
            "risk_impact": attr.risk_impact if attr else 0.0,
            "risk_breakdown": {
                "schedule": attr.schedule_loss if attr else 0.0,
                "rfc": attr.rfc_loss if attr else 0.0,
                "permit": attr.permit_loss if attr else 0.0,
                "idle": attr.idle_loss if attr else 0.0,
            } if attr else None,
            "claim_ids": r.claim_ids,
            "change_order_ids": r.change_order_ids,
            "double_count_risk": r.double_count_risk,
            "fully_de_risked": r.fully_de_risked,
        })
    # Activities with risk impact but no exposure (off-claim/off-CO drag)
    seen = {r.activity_id for r in rows}
    for aid, attr in attribs.items():
        if aid in seen or attr.risk_impact <= 0:
            continue
        items.append({
            "activity_id": aid,
            "gross_claim_impact": _m(0.0),
            "approved_co_recovery": _m(0.0),
            "net_exposure": _m(0.0),
            "risk_impact": attr.risk_impact,
            "risk_breakdown": {
                "schedule": attr.schedule_loss,
                "rfc": attr.rfc_loss,
                "permit": attr.permit_loss,
                "idle": attr.idle_loss,
            },
            "claim_ids": [],
            "change_order_ids": [],
            "double_count_risk": False,
            "fully_de_risked": False,
        })
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


@router.get(
    "/heatmap",
    summary="CEO Heatmap — Risk Impact × Net Exposure quadrants",
    description=(
        "Returns one cell per activity carrying risk or exposure, plus the "
        "thresholds used to bucket each cell. Activities sitting in HH "
        "(high risk + high exposure) for ≥ 48 hours auto-fire a Tier-3 "
        "nuclear notification when this endpoint runs in alert mode."
    ),
)
def heatmap(
    project_id: int = Query(...),
    fire_alerts: bool = Query(True),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(*list(TechnicalRole))),
) -> dict:
    cells = risk_heatmap.evaluate(db, project_id, fire_alerts=fire_alerts)
    allowed = margin_mask.get_policy().fields_for(user.role)
    show_money = FinancialField.DELAY_CLAIM_VALUE in allowed

    def _m(v: float) -> float | None:
        return v if show_money else None

    return {
        "project_id": project_id,
        "thresholds": {
            "high_risk_score_points": risk_heatmap.HIGH_RISK_THRESHOLD,
            "high_exposure_dollars": risk_heatmap.HIGH_EXPOSURE_THRESHOLD,
            "dwell_hours_nuclear": risk_heatmap.DWELL_HOURS_NUCLEAR,
        },
        "cells": [
            {
                "activity_id": c.activity_id,
                "risk_impact": c.risk_impact,
                "net_exposure": _m(c.net_exposure),
                "quadrant": c.quadrant,
                "entered_at": c.entered_at.isoformat(),
                "hours_in_quadrant": round(c.hours_in_quadrant, 2),
                "claim_ids": c.claim_ids,
                "change_order_ids": c.change_order_ids,
            }
            for c in cells
        ],
    }


@router.get(
    "/heatmap/cells/{activity_id}/evidence",
    summary="Black Box evidence panel for a heatmap cell",
    description=(
        "Returns the communication trail (Black Box), a hashed audit trail "
        "with per-row SHA-256 + a roll-up bundle hash, and a per-org "
        "Subcontractor Scorecard. The CEO's heatmap UI calls this when a "
        "cell is clicked so the recipient sees the full evidence chain "
        "(emails, mentions, status changes) without leaving the page."
    ),
)
def heatmap_evidence(
    activity_id: str,
    project_id: int = Query(...),
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_role(*list(TechnicalRole))),
) -> dict:
    bundle = evidence_bridge.build(db, project_id=project_id, activity_id=activity_id)

    # Mask money in the scorecard if the viewer can't see DELAY_CLAIM_VALUE.
    allowed = margin_mask.get_policy().fields_for(user.role)
    show_money = FinancialField.DELAY_CLAIM_VALUE in allowed

    def _row_for(s):
        return {
            "org": s.org,
            "role": s.role,
            "rfc_total": s.rfc_total,
            "rfc_on_time": s.rfc_on_time,
            "rfc_on_time_pct": s.rfc_on_time_pct,
            "claim_count": s.claim_count,
            "claim_gross_total": s.claim_gross_total if show_money else None,
            "claim_net_total": s.claim_net_total if show_money else None,
            "co_count": s.co_count,
            "co_approved": s.co_approved,
            "co_approval_pct": s.co_approval_pct,
            "double_count_flagged": s.double_count_flagged,
        }

    return {
        "project_id": bundle.project_id,
        "activity_id": bundle.activity_id,
        "generated_at": bundle.generated_at,
        "bundle_hash": bundle.bundle_hash,
        "communications": [c.__dict__ for c in bundle.communications],
        "audit_trail": [a.__dict__ for a in bundle.audit_trail],
        "scorecard": [_row_for(s) for s in bundle.scorecard],
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
