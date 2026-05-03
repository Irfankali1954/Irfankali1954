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
    assert FinancialField.INTERNAL_MARGIN in pol.fields_for(TechnicalRole.CFO)
    assert FinancialField.INTERNAL_MARGIN not in pol.fields_for(TechnicalRole.ADMIN)


# ---------- cross-pollination: RFC miss → field idle → wrap risk drop -------

def _fresh_db():
    """In-memory SQLite for isolated wrap-risk simulation tests."""
    from datetime import datetime as _dt, timedelta, timezone as _tz

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.session import Base
    import app.models  # register tables  # noqa: F401
    from app.models.financial import Project
    from app.models.risk import RFCDrawing
    from app.models.schedule import ScheduleActivity, CriticalPathSnapshot

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = _dt.now(_tz.utc)

    project = Project(
        id=1, code="P-1", name="Test", cod_target=now + timedelta(days=365),
        contract_type="EPC_LSTK", contract_value=100_000_000, budget_total=80_000_000,
    )
    rfc = RFCDrawing(
        id=1, project_id=1, drawing_no="X-102", title="Foundation",
        discipline="civil", issuer_org="ExtCo", rfc_due=now + timedelta(days=10),
    )
    act = ScheduleActivity(
        project_id=1, activity_id="CIV-1040", name="Pour", wbs="1.2.3",
        planned_start=now, planned_finish=now + timedelta(days=15),
        duration_days=15, predecessors=[], successors=[],
    )
    cpm = CriticalPathSnapshot(
        project_id=1, computed_at=now, project_finish=now + timedelta(days=15),
        critical_activity_ids=["CIV-1040"], total_float_days=10.0, trigger="seed",
    )
    db.add_all([project, rfc, act, cpm])
    db.commit()
    return db


def test_rfc_miss_simulation_drops_wrap_score():
    """The flagship cross-pollination invariant.

    A simulated RFC miss MUST lower the Wrap Risk Score and produce a
    non-zero idle cost — proving the Engineering→Field→Score chain works.
    """
    from app.services import wrap_risk

    db = _fresh_db()
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=10, idle_crew=15, crew_burdened_rate=140.0,
    )
    assert sim.before_score > 0
    assert sim.after_score < sim.before_score, "wrap score must fall after RFC miss"
    assert sim.delta < 0
    assert sim.idle_cost > 0
    # Both factors must move in the right direction.
    assert sim.factors_after.rfc <= sim.factors_before.rfc
    assert sim.factors_after.field_idle <= sim.factors_before.field_idle


def test_factor_weights_sum_to_one():
    from app.services.wrap_risk import WEIGHTS
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
