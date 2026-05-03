"""Smoke tests covering the core invariants of the scaffold."""

from datetime import datetime, timedelta, timezone

from app.core.rbac import (
    FinancialField,
    TechnicalRole,
    default_visibility_policy,
)
from app.schemas.financial import ProjectFinancialSummary
from app.services import margin_mask
from app.services.field_idle_cost import compute_idle_cost


def test_admin_cannot_see_margin_by_default():
    margin_mask.set_policy(default_visibility_policy())
    s = ProjectFinancialSummary(
        project_id=1, code="P1",
        revenue=1_000_000, actual_cost=900_000,
        margin=100_000, margin_percent=10.0, field_idle_cost=5_000,
    )
    masked = margin_mask.apply_visibility(s, TechnicalRole.ADMIN)
    assert masked.margin is None
    assert masked.margin_percent is None
    assert masked.revenue is None  # admin sees no money by default


def test_cfo_sees_everything_by_default():
    margin_mask.set_policy(default_visibility_policy())
    s = ProjectFinancialSummary(
        project_id=1, code="P1",
        revenue=1_000_000, actual_cost=900_000,
        margin=100_000, margin_percent=10.0, field_idle_cost=5_000,
    )
    cfo_view = margin_mask.apply_visibility(s, TechnicalRole.CFO)
    assert cfo_view.margin == 100_000
    assert cfo_view.revenue == 1_000_000


def test_subcontractor_sees_no_money():
    margin_mask.set_policy(default_visibility_policy())
    s = ProjectFinancialSummary(
        project_id=1, code="P1",
        revenue=1_000_000, actual_cost=900_000,
        margin=100_000, margin_percent=10.0, field_idle_cost=5_000,
    )
    sub_view = margin_mask.apply_visibility(s, TechnicalRole.SUBCONTRACTOR)
    assert sub_view.revenue is None
    assert sub_view.margin is None
    assert sub_view.field_idle_cost is None


def test_field_idle_cost_zero_when_not_overdue():
    rfc_due = datetime.now(timezone.utc) + timedelta(days=2)
    cost = compute_idle_cost(
        rfc_due=rfc_due, idle_crew=10, crew_burdened_rate=120.0, equipment_rates=[300.0],
    )
    assert cost.total == 0.0


def test_field_idle_cost_compounds_after_due():
    rfc_due = datetime.now(timezone.utc) - timedelta(days=1)
    cost = compute_idle_cost(
        rfc_due=rfc_due, idle_crew=10, crew_burdened_rate=120.0, equipment_rates=[300.0, 200.0],
    )
    assert cost.idle_hours > 0
    assert cost.crew_cost > 0
    assert cost.equipment_cost > 0
    assert cost.total == cost.crew_cost + cost.equipment_cost


def test_visibility_policy_round_trip():
    margin_mask.set_policy(default_visibility_policy())
    pol = margin_mask.get_policy()
    assert FinancialField.MARGIN in pol.fields_for(TechnicalRole.CFO)
    assert FinancialField.MARGIN not in pol.fields_for(TechnicalRole.ADMIN)
