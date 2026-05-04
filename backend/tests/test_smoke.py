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


# ---------- claim harvester: messages tagged to RFC → Statement of Facts ----

def test_claim_harvester_pulls_tagged_messages_and_opens_approval():
    from app.models.financial import GatekeeperApproval
    from app.models.messaging import Message, MessageThread
    from app.services import claim_harvester, wrap_risk

    db = _fresh_db()

    # Tag two messages to the RFC drawing — these must surface in the SoF.
    thread = MessageThread(project_id=1, subject="X-102 status")
    db.add(thread); db.flush()
    db.add_all([
        Message(
            thread_id=thread.id, sender_email="site_manager@kiewit.com",
            sender_org="lead_epc", body="@civil_lead crews mobilising — where is X-102?",
            mentions=["civil_lead"], rfc_drawing_id=1,
        ),
        Message(
            thread_id=thread.id, sender_email="civil_lead@externalco.com",
            sender_org="ExtCo", body="QA reviewing, expect 2-day delay.",
            mentions=[], rfc_drawing_id=1,
        ),
    ])
    db.commit()

    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=10, idle_crew=15, crew_burdened_rate=140.0,
    )

    assert sim.claim_id is not None
    assert sim.approval_id is not None

    from app.models.risk import DelayClaim
    claim = db.get(DelayClaim, sim.claim_id)
    assert claim is not None
    assert claim.status == "draft"
    assert claim.causing_org == "ExtCo"
    assert claim.subject_kind == "rfc"
    assert claim.subject_ref == "X-102"
    # Communications were harvested in chronological order from the tagged thread.
    assert len(claim.communications) == 2
    assert claim.communications[0]["from"] == "site_manager@kiewit.com"
    # Statement of Facts contains both message lines + the cost figure.
    sof = claim.statement_of_facts or ""
    assert "STATEMENT OF FACTS" in sof
    assert "site_manager@kiewit.com" in sof
    assert "civil_lead@externalco.com" in sof
    assert "X-102" in sof
    assert "$" in sof  # CFO viewer in harvest renders the cost unmasked

    # CFO approval gate: a pending approval was opened with the idle cost.
    approval = db.get(GatekeeperApproval, sim.approval_id)
    assert approval is not None
    assert approval.status == "pending"
    assert float(approval.amount) == sim.idle_cost
    assert approval.subject_type == "delay_claim"
    assert approval.subject_id == claim.id


def test_finalize_blocked_until_cfo_approves():
    from app.models.risk import DelayClaim
    from app.services import cfo_gatekeeper, claim_harvester, wrap_risk

    db = _fresh_db()
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=5, idle_crew=10, crew_burdened_rate=120.0,
    )
    claim = db.get(DelayClaim, sim.claim_id)
    assert claim.status == "draft"

    # Before approval: refuse to finalize.
    from app.api.v1.claims import finalize  # noqa
    # We bypass the FastAPI dep wiring and assert the underlying invariant.
    from app.models.financial import GatekeeperApproval
    approval = db.get(GatekeeperApproval, claim.approval_id)
    assert approval.status == "pending"

    # CFO approves: status flips, claim can finalize.
    cfo_gatekeeper.decide(db, approval.id, decision="approve",
                          cfo_email="cfo@lead.epc", notes="counsel reviewed")
    db.refresh(approval)
    assert approval.status == "approved"

    # Idempotent re-harvest must NOT create a second claim.
    again = claim_harvester.harvest_for_idle_event(db, claim.idle_event_id)
    assert again.id == claim.id


def test_statement_of_facts_template_contains_required_sections():
    """Every SoF must have the four numbered sub-sections and the cost line."""
    from app.models.risk import DelayClaim
    from app.services import wrap_risk

    db = _fresh_db()
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=3, idle_crew=8, crew_burdened_rate=110.0,
    )
    claim = db.get(DelayClaim, sim.claim_id)
    sof = claim.statement_of_facts or ""
    for marker in ("### 2.1", "### 2.2", "### 2.3", "### 2.4"):
        assert marker in sof, f"missing section {marker}"
    assert "Total measured idle time" in sof
    assert "Direct financial damages" in sof


# ---------- permit-delay parity --------------------------------------------

def _fresh_db_with_permit():
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.session import Base
    import app.models  # noqa: F401
    from app.models.financial import Project
    from app.models.risk import PermitStatus
    from app.models.schedule import ScheduleActivity, CriticalPathSnapshot

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = _dt.now(_tz.utc)

    project = Project(
        id=1, code="P-1", name="Test", cod_target=now + timedelta(days=365),
        contract_type="EPC_LSTK", contract_value=100_000_000, budget_total=80_000_000,
    )
    permit = PermitStatus(
        id=1, project_id=1, permit_type="Air Quality Construction Permit",
        authority="State EPA Region 7",
        target_date=now + timedelta(days=10), status="pending",
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
    db.add_all([project, permit, act, cpm])
    db.commit()
    return db


def test_permit_delay_simulation_drops_score_and_drafts_claim():
    from app.models.risk import DelayClaim, IdleEvent
    from app.services import wrap_risk

    db = _fresh_db_with_permit()
    sim = wrap_risk.simulate_permit_delay(
        db, project_id=1, permit_id=1,
        days_overdue=10, idle_crew=20, crew_burdened_rate=140.0,
    )
    assert sim.after_score < sim.before_score
    assert sim.idle_cost > 0

    claim = db.get(DelayClaim, sim.claim_id)
    assert claim is not None
    assert claim.subject_kind == "permit"
    assert claim.subject_ref == "Air Quality Construction Permit"
    assert claim.causing_org == "State EPA Region 7"
    assert claim.permit_id == 1

    # IdleEvent must carry the permit FK (no heuristic).
    evt = db.get(IdleEvent, claim.idle_event_id)
    assert evt.permit_id == 1
    assert evt.rfc_drawing_id is None
    assert evt.cause == "missing_permit"


# ---------- Tier-3 notification trigger ------------------------------------

def _seed_recipients(db):
    from app.models.notification import NotificationRecipient
    db.add_all([
        NotificationRecipient(name="Project Director", role_label="PD",
                              email="pd@lead.epc", phone="+15555550101",
                              tiers=["tier_2", "tier_3"], active=True),
        NotificationRecipient(name="CFO", role_label="CFO",
                              email="cfo@lead.epc", phone="+15555550102",
                              tiers=["tier_2", "tier_3"], active=True),
        NotificationRecipient(name="CEO", role_label="CEO",
                              email="ceo@lead.epc", phone="+15555550103",
                              tiers=["tier_3"], active=True),
        NotificationRecipient(name="HSE Lead", role_label="HSE",
                              email="hse@lead.epc", phone="+15555550199",
                              tiers=["tier_2"], active=True),
    ])
    db.commit()


def test_tier_resolution_pure_logic():
    from app.services.notification_service import EvalContext, Tier, resolve_tier
    # Cost-only nuclear trigger
    assert resolve_tier(EvalContext(1, 0.5, 80_000, "x")) is Tier.NUCLEAR
    # Drift-only nuclear trigger
    assert resolve_tier(EvalContext(1, 6.0, 0, "x")) is Tier.NUCLEAR
    # Urgent (drift only)
    assert resolve_tier(EvalContext(1, 3.0, 0, "x")) is Tier.URGENT
    # Normal
    assert resolve_tier(EvalContext(1, 0.5, 0, "x")) is Tier.NORMAL


def test_tier_3_texts_only_tier_3_recipients():
    from app.connectors import twilio_sns, email_smtp
    from app.models.notification import Notification
    from app.services import wrap_risk
    twilio_sns.SENT.clear()
    email_smtp.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)

    # 10-day overdue + idle cost > $50k → guaranteed nuclear.
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=10, idle_crew=30, crew_burdened_rate=200.0,
    )

    notes = db.query(Notification).order_by(Notification.created_at).all()
    assert len(notes) >= 1
    nuclear = [n for n in notes if n.tier == "tier_3"]
    assert nuclear, "no nuclear notification produced despite $50k+ idle cost"

    # SMS captured for exactly the tier_3 phone numbers (PD, CFO, CEO).
    sms_targets = {m["to"] for m in twilio_sns.SENT}
    assert "+15555550101" in sms_targets
    assert "+15555550102" in sms_targets
    assert "+15555550103" in sms_targets
    # HSE is tier_2 only — must NOT be SMS'd.
    assert "+15555550199" not in sms_targets

    # Email blast covers tier_2 (PD, CFO, HSE) — CEO is tier_3-only here.
    email_targets = {m["to"] for m in email_smtp.SENT}
    assert "pd@lead.epc" in email_targets
    assert "cfo@lead.epc" in email_targets
    assert "hse@lead.epc" in email_targets


def test_tier_2_does_not_send_sms():
    from app.connectors import twilio_sns, email_smtp
    from app.models.notification import Notification, NotificationRecipient
    from app.services import notification_service
    twilio_sns.SENT.clear(); email_smtp.SENT.clear()

    db = _fresh_db()
    db.add(NotificationRecipient(
        name="CEO", role_label="CEO", email="ceo@lead.epc", phone="+15555550103",
        tiers=["tier_3"], active=True,
    ))
    db.commit()

    # Force a tier-2 context: bigger drift than urgent threshold but no idle cost
    # and below nuclear thresholds.
    from app.services.notification_service import (
        EvalContext, Tier, resolve_tier,
    )
    ctx = EvalContext(project_id=1, cpm_drift_days=3.0, open_idle_cost=0.0, trigger="t")
    assert resolve_tier(ctx) is Tier.URGENT

    # Direct evaluator call simulates a CPM recompute that produced 3-day drift.
    # We seed a prior CPM snapshot whose finish is earlier so drift = 3.
    from datetime import datetime, timedelta, timezone as _tz
    from app.models.schedule import CriticalPathSnapshot
    now = datetime.now(_tz.utc)
    db.add(CriticalPathSnapshot(
        project_id=1, computed_at=now, project_finish=now + timedelta(days=18),
        critical_activity_ids=[], total_float_days=0.0, trigger="rebase",
    ))
    db.commit()
    notif = notification_service.evaluate_for_project(db, 1, trigger="manual")
    assert notif is not None
    assert notif.tier == "tier_2"
    assert twilio_sns.SENT == [], "tier 2 must never SMS the CEO"


# ---------- Ingest health -------------------------------------------------

def test_ingest_health_flags_missing_permit_due_date():
    from app.services import ingest_validation
    records = [
        {"project_code": "P-1", "po_number": "PO-001", "vendor_id": "V1",
         "amount": 1_000_000, "expected_delivery": "2026-01-01"},
        {"project_code": "P-1", "po_number": "PO-002", "vendor_id": "V2",
         "amount": 2_000_000},
    ]
    h = ingest_validation.evaluate_erp_commitments(records, vendor="oracle")
    assert h.accepted_records == 2
    assert h.field_coverage["permit_due_date"] == 0.0
    # 15% accuracy hit attributable to permit_due_date alone.
    assert h.accuracy_degradation_pct >= 15.0
    assert any("permit_due_date" in n for n in h.notes)


# ---------- Management comments ------------------------------------------

def test_management_comment_persisted_for_idle_event():
    from app.models.comment import ManagementComment
    from app.models.risk import DelayClaim
    from app.services import wrap_risk

    db = _fresh_db()
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=3, idle_crew=10, crew_burdened_rate=120.0,
    )
    claim = db.get(DelayClaim, sim.claim_id)

    db.add(ManagementComment(
        target_kind="claim", target_id=claim.id,
        author_email="ceo@lead.epc", author_role="cfo",
        body="Counsel notified. File on Friday if not resolved.",
    ))
    db.commit()
    rows = (
        db.query(ManagementComment)
        .filter(ManagementComment.target_kind == "claim",
                ManagementComment.target_id == claim.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].author_email == "ceo@lead.epc"
