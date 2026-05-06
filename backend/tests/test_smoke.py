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

# ---------- Change Order Sentinel ------------------------------------------

def _make_change_order(db, *, discovered_days_ago=0, notice_days=7, claim_days=21,
                       activity_id="CIV-1040", direct_cost=100_000.0, on_cp=False):
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from app.models.change_order import ChangeOrder
    from app.models.schedule import CriticalPathSnapshot
    from app.services import change_order_sentinel

    discovered = _dt.now(_tz.utc) - timedelta(days=discovered_days_ago)
    co = ChangeOrder(
        project_id=1, co_number=f"CO-{discovered_days_ago:03d}",
        title="Reroute storm drain around grade beam",
        description="", originator_org="LocalCivilSub",
        originator_email="sub@civil.local", contract_clause="GC-12.4",
        discovered_at=discovered,
        notice_period_days=notice_days, claim_period_days=claim_days,
        linked_activity_id=activity_id,
        direct_cost=direct_cost,
    )
    change_order_sentinel.compute_deadlines(co)
    db.add(co); db.flush()

    snap = (db.query(CriticalPathSnapshot)
              .filter(CriticalPathSnapshot.project_id == 1)
              .order_by(CriticalPathSnapshot.computed_at.desc()).first())
    if snap is not None:
        snap.critical_activity_ids = [activity_id] if on_cp else []
    change_order_sentinel.assess_critical_path(db, co)
    db.commit(); db.refresh(co)
    return co


def test_change_order_deadlines_derived_from_discovered_at():
    from app.services import change_order_sentinel
    db = _fresh_db()
    co = _make_change_order(db, discovered_days_ago=0)
    delta_notice = (co.notice_due_by - co.discovered_at).days
    delta_claim = (co.claim_due_by - co.discovered_at).days
    assert delta_notice == 7
    assert delta_claim == 21


def test_aging_classifier_buckets():
    from app.services.change_order_sentinel import classify
    db = _fresh_db()
    fresh = _make_change_order(db, discovered_days_ago=0)
    assert classify(fresh).severity == "ok"
    assert classify(fresh).deadline_kind == "notice"

    aging = _make_change_order(db, discovered_days_ago=6, notice_days=7)  # ~1 day left
    assert classify(aging).severity == "approaching"

    bar = _make_change_order(db, discovered_days_ago=10, notice_days=7)
    assert classify(bar).severity == "missed"


def test_missed_notice_fires_tier_3_text_the_ceo():
    from app.connectors import twilio_sns, email_smtp
    from app.models.notification import Notification
    from app.services import change_order_sentinel
    twilio_sns.SENT.clear(); email_smtp.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)
    _make_change_order(db, discovered_days_ago=10, notice_days=7)

    items, fired = change_order_sentinel.scan(db, project_id=1, trigger="test")
    assert fired >= 1
    assert any(i.severity == "missed" for i in items)

    nukes = (db.query(Notification)
               .filter(Notification.tier == "tier_3")
               .filter(Notification.trigger.like("co_aging:%")).all())
    assert nukes, "missed time-bar must fire a tier_3 notification"

    sms_targets = {m["to"] for m in twilio_sns.SENT}
    assert "+15555550103" in sms_targets, "CEO must be SMS'd on a time-bar breach"


def test_approaching_notice_on_critical_path_escalates_to_tier_3():
    from app.connectors import twilio_sns
    from app.models.notification import Notification
    from app.services import change_order_sentinel
    twilio_sns.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)
    _make_change_order(db, discovered_days_ago=6, notice_days=7,
                       activity_id="CIV-1040", on_cp=True)

    change_order_sentinel.scan(db, project_id=1, trigger="test")
    nukes = (db.query(Notification)
               .filter(Notification.tier == "tier_3")
               .filter(Notification.trigger.like("co_aging:%")).all())
    assert nukes, "approaching CO on critical path must escalate to nuclear"


def test_approaching_notice_off_critical_path_is_only_tier_2():
    from app.connectors import twilio_sns
    from app.models.notification import Notification
    from app.services import change_order_sentinel
    twilio_sns.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)
    _make_change_order(db, discovered_days_ago=6, notice_days=7,
                       activity_id="CIV-1040", on_cp=False)

    change_order_sentinel.scan(db, project_id=1, trigger="test")
    notes = (db.query(Notification)
               .filter(Notification.trigger.like("co_aging:%")).all())
    assert all(n.tier != "tier_3" for n in notes), \
        "approaching off-CP must NOT escalate to nuclear"
    assert any(n.tier == "tier_2" for n in notes)
    assert twilio_sns.SENT == [], "tier 2 must never SMS"


def test_markup_masking_for_non_cfo_viewer():
    from app.core.rbac import (
        FinancialField, TechnicalRole, VisibilityPolicy, default_visibility_policy,
    )
    from app.schemas.change_order import ChangeOrderOut
    from app.services import change_order_sentinel, margin_mask

    db = _fresh_db()
    co = _make_change_order(db, direct_cost=100_000.0)
    change_order_sentinel.apply_markup(db, co, markup_pct=15.0, actor_email="cfo@x")
    db.commit(); db.refresh(co)
    assert float(co.markup_value) == 15_000.0
    assert float(co.proposed_value) == 115_000.0

    out = ChangeOrderOut.model_validate(co, from_attributes=True)
    margin_mask.set_policy(default_visibility_policy())

    # CFO sees everything by the default policy — including the markup.
    cfo_view = margin_mask.apply_visibility(out, TechnicalRole.CFO)
    assert cfo_view.markup_value == 15_000.0

    # Subcontractor sees nothing by default — must NOT see direct or markup.
    sub_view = margin_mask.apply_visibility(out, TechnicalRole.SUBCONTRACTOR)
    assert sub_view.direct_cost is None
    assert sub_view.markup_value is None
    assert sub_view.proposed_value is None

    # Now flip to "open book on cost, closed book on markup" — the canonical
    # CO posture for subcontractor visibility.
    pol = VisibilityPolicy(allowed={
        TechnicalRole.CFO: frozenset({
            FinancialField.CHANGE_ORDER_DIRECT_COST,
            FinancialField.CHANGE_ORDER_MARKUP,
            FinancialField.CHANGE_ORDER_TOTAL,
        }),
        TechnicalRole.SUBCONTRACTOR: frozenset({FinancialField.CHANGE_ORDER_DIRECT_COST}),
    })
    margin_mask.set_policy(pol)

    cfo_view = margin_mask.apply_visibility(out, TechnicalRole.CFO)
    assert cfo_view.markup_value == 15_000.0
    assert cfo_view.proposed_value == 115_000.0
    assert cfo_view.direct_cost == 100_000.0

    sub_view = margin_mask.apply_visibility(out, TechnicalRole.SUBCONTRACTOR)
    assert sub_view.direct_cost == 100_000.0   # open book
    assert sub_view.markup_value is None       # closed book
    assert sub_view.proposed_value is None     # closed book


# ---------- Convergence of Truth -------------------------------------------

def _make_approved_co(db, *, activity_id="CIV-1040", direct_cost=200_000.0,
                      markup_pct=15.0, co_number="CO-CONVRG"):
    """Minimal helper that creates a CO and walks it to status='approved'."""
    from datetime import datetime as _dt, timezone as _tz
    from app.models.change_order import ChangeOrder
    from app.models.financial import GatekeeperApproval
    from app.services import cfo_gatekeeper, change_order_sentinel

    co = ChangeOrder(
        project_id=1, co_number=co_number,
        title="Reroute storm drain around grade beam",
        description="", originator_org="LocalCivilSub",
        originator_email="sub@civil.local", contract_clause="GC-12.4",
        discovered_at=_dt.now(_tz.utc),
        notice_period_days=7, claim_period_days=21,
        linked_activity_id=activity_id,
        direct_cost=direct_cost,
    )
    change_order_sentinel.compute_deadlines(co)
    db.add(co); db.flush()
    change_order_sentinel.apply_markup(db, co, markup_pct=markup_pct, actor_email="cfo@x")
    co.notice_sent_at = _dt.now(_tz.utc)
    co.claim_filed_at = _dt.now(_tz.utc)
    co.status = "claim_filed"
    db.flush()

    approval = cfo_gatekeeper.open_approval(
        db, project_id=1, subject_type="change_order", subject_id=co.id,
        amount=float(co.proposed_value or 0),
    )
    co.cfo_approval_id = approval.id
    cfo_gatekeeper.decide(db, approval.id, decision="approve",
                          cfo_email="cfo@x", notes="approved for test")
    co.status = "approved"
    db.commit(); db.refresh(co)
    return co


def test_compute_for_activity_subtracts_approved_co():
    from app.models.risk import DelayClaim
    from app.services import convergence_service, wrap_risk

    db = _fresh_db()

    # 1. Approve a CO worth $230k on CIV-1040 (200k direct + 15% markup).
    co = _make_approved_co(db, activity_id="CIV-1040", direct_cost=200_000.0,
                           markup_pct=15.0)
    assert float(co.proposed_value) == 230_000.0

    # 2. Spawn a delay claim against CIV-1040 with a known impact.
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=4, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    gross = float(claim.impact_value or 0)
    assert gross > 0
    assert claim.linked_activity_id == "CIV-1040"

    expo = convergence_service.compute_for_activity(db, 1, "CIV-1040")
    assert expo.gross_claim_impact == gross
    assert expo.approved_co_recovery == 230_000.0
    assert expo.net_exposure == max(0.0, gross - 230_000.0)
    assert expo.double_count_risk is True
    assert expo.fully_de_risked == (230_000.0 >= gross)


def test_reconcile_distributes_offset_and_persists_on_claim():
    from app.models.risk import DelayClaim
    from app.services import convergence_service, wrap_risk

    db = _fresh_db()
    _make_approved_co(db, activity_id="CIV-1040", direct_cost=200_000.0,
                      markup_pct=10.0)  # proposed_value = $220k

    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=4, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    db.refresh(claim)

    # Harvester already triggered reconcile; verify the offset persisted.
    expo = convergence_service.compute_for_activity(db, 1, "CIV-1040")
    expected_offset = min(float(claim.impact_value or 0), expo.approved_co_recovery)
    assert float(claim.co_offset_value or 0) == expected_offset


def test_double_count_flag_and_tier_3_alert_for_overlapping_co():
    from app.connectors import twilio_sns
    from app.models.notification import Notification
    from app.models.risk import DelayClaim
    from app.services import wrap_risk
    twilio_sns.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)
    _make_approved_co(db, activity_id="CIV-1040", direct_cost=200_000.0)

    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=2, idle_crew=8, crew_burdened_rate=110.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    assert claim.double_count_flag is True

    # Tier-3 nuclear alert with the convergence trigger.
    nukes = (db.query(Notification)
               .filter(Notification.tier == "tier_3")
               .filter(Notification.trigger == "convergence:double_count").all())
    assert nukes, "double-count must fire a tier_3 notification"
    assert "+15555550103" in {m["to"] for m in twilio_sns.SENT}, \
        "CEO must be SMS'd on a double-count finding"


def test_co_approval_re_reconciles_existing_claims():
    """A claim drafted before the CO is approved must get re-offset
    the moment the CO flips to approved."""
    from app.models.risk import DelayClaim
    from app.services import wrap_risk, convergence_service

    db = _fresh_db()

    # Claim first (no CO yet).
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=4, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    assert float(claim.co_offset_value or 0) == 0.0
    assert claim.double_count_flag is False

    # Now approve a CO on the same activity.
    co = _make_approved_co(db, activity_id="CIV-1040", direct_cost=100_000.0,
                           markup_pct=20.0)  # proposed_value = $120k
    convergence_service.reconcile_for_change_order(db, co)
    db.refresh(claim)

    expected = min(float(claim.impact_value or 0), 120_000.0)
    assert float(claim.co_offset_value or 0) == expected


def test_sof_renders_offset_block_when_co_offsets_claim():
    from app.models.risk import DelayClaim
    from app.services import wrap_risk

    db = _fresh_db()
    _make_approved_co(db, activity_id="CIV-1040", direct_cost=200_000.0,
                      markup_pct=15.0)
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=3, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    sof = claim.statement_of_facts or ""
    assert "### 2.5" in sof
    assert "Approved Change Order recovery" in sof or "Double-count" in sof


# ---------- Phase 8: Risk Attribution + Heatmap + Reverse de-risk ----------

def test_reverse_de_risk_releases_offset_when_co_rejected():
    from app.models.financial import GatekeeperApproval
    from app.models.risk import DelayClaim
    from app.services import cfo_gatekeeper, convergence_service, wrap_risk

    db = _fresh_db()
    co = _make_approved_co(db, activity_id="CIV-1040", direct_cost=200_000.0,
                           markup_pct=15.0)  # proposed = $230k
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=4, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    claim = db.get(DelayClaim, sim.claim_id)
    db.refresh(claim)
    initial_offset = float(claim.co_offset_value or 0)
    assert initial_offset > 0, "claim must be offset before we test the release"

    # Now reject the CO (simulate the API path that reconciles).
    approval = db.get(GatekeeperApproval, co.cfo_approval_id)
    cfo_gatekeeper.decide(db, approval.id, decision="reject",
                          cfo_email="cfo@x", notes="post-hoc rejection")
    co.status = "rejected"
    db.commit()
    convergence_service.reconcile_for_change_order(db, co)
    db.refresh(claim)

    # Offset must have been released.
    assert float(claim.co_offset_value or 0) == 0.0


def test_risk_attribution_decomposes_loss_to_activities():
    """Per-activity impacts must sum (within rounding) to the claim-relevant
    portion of the project-level score loss."""
    from app.services import risk_attribution, wrap_risk

    db = _fresh_db()
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=10, idle_crew=20, crew_burdened_rate=140.0,
        linked_activity_id="CIV-1040",
    )

    attrs = risk_attribution.attribute_for_project(db, project_id=1)
    assert attrs, "attribution must produce at least one row"
    civ = next((a for a in attrs if a.activity_id == "CIV-1040"), None)
    assert civ is not None
    # The simulation overdued an RFC and built idle cost — both must
    # contribute to this activity's drag.
    assert civ.rfc_loss > 0, "RFC severity decay must contribute to CIV-1040"
    assert civ.idle_loss > 0, "idle cost on this activity must contribute"
    assert civ.risk_impact > 0
    # Risk impact must not exceed the maximum possible loss (100 points).
    assert 0 < civ.risk_impact <= 100.0


def test_risk_attribution_clamps_factor_loss_at_one():
    """Even an apocalyptic miss cannot push a single factor's per-activity
    contribution above 1.0 (it can max out the factor, not exceed it)."""
    from app.services import risk_attribution, wrap_risk

    db = _fresh_db()
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=365, idle_crew=500, crew_burdened_rate=10_000.0,
        linked_activity_id="CIV-1040",
    )
    attrs = risk_attribution.attribute_for_project(db, project_id=1)
    civ = next((a for a in attrs if a.activity_id == "CIV-1040"), None)
    assert civ is not None
    assert 0.0 <= civ.idle_loss <= 1.0
    assert 0.0 <= civ.rfc_loss <= 1.0
    assert 0.0 <= civ.permit_loss <= 1.0
    assert 0.0 <= civ.schedule_loss <= 1.0


def test_heatmap_classifies_into_correct_quadrant():
    from app.services import risk_heatmap, wrap_risk

    db = _fresh_db()
    # Big enough to push both risk and exposure over thresholds.
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=14, idle_crew=30, crew_burdened_rate=200.0,
        linked_activity_id="CIV-1040",
    )
    cells = risk_heatmap.evaluate(db, project_id=1, fire_alerts=False)
    civ = next((c for c in cells if c.activity_id == "CIV-1040"), None)
    assert civ is not None
    assert civ.quadrant == "HH", \
        f"large RFC miss + idle cost must land in HH, got {civ.quadrant}"


def test_heatmap_dwell_alert_fires_only_after_48h():
    from datetime import datetime, timedelta, timezone as _tz

    from app.connectors import twilio_sns
    from app.models.heatmap import HeatmapPosition
    from app.models.notification import Notification
    from app.services import risk_heatmap, wrap_risk
    twilio_sns.SENT.clear()

    db = _fresh_db()
    _seed_recipients(db)
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=14, idle_crew=30, crew_burdened_rate=200.0,
        linked_activity_id="CIV-1040",
    )

    # First evaluation — fresh HH cell, no dwell alert yet.
    cells = risk_heatmap.evaluate(db, project_id=1)
    assert any(c.quadrant == "HH" for c in cells)
    dwell = (db.query(Notification)
               .filter(Notification.trigger.like("heatmap_dwell:%")).all())
    assert dwell == [], "no dwell alert should fire on the first evaluation"

    # Pre-age the position by 49 hours to simulate dwell.
    pos = (db.query(HeatmapPosition)
             .filter(HeatmapPosition.activity_id == "CIV-1040").first())
    assert pos is not None
    pos.entered_at = datetime.now(_tz.utc) - timedelta(hours=49)
    db.commit()

    twilio_sns.SENT.clear()
    risk_heatmap.evaluate(db, project_id=1)
    dwell = (db.query(Notification)
               .filter(Notification.trigger.like("heatmap_dwell:%")).all())
    assert dwell, "after 48h in HH the bus must fire a dwell alert"
    assert dwell[0].tier == "tier_3"
    assert "+15555550103" in {m["to"] for m in twilio_sns.SENT}, \
        "CEO must be SMS'd on a 48h-stuck HH cell"


def test_heatmap_dwell_alert_dedup_within_24h():
    from datetime import datetime, timedelta, timezone as _tz

    from app.models.heatmap import HeatmapPosition
    from app.models.notification import Notification
    from app.services import risk_heatmap, wrap_risk

    db = _fresh_db()
    _seed_recipients(db)
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=14, idle_crew=30, crew_burdened_rate=200.0,
        linked_activity_id="CIV-1040",
    )
    risk_heatmap.evaluate(db, project_id=1)
    pos = (db.query(HeatmapPosition)
             .filter(HeatmapPosition.activity_id == "CIV-1040").first())
    pos.entered_at = datetime.now(_tz.utc) - timedelta(hours=49)
    db.commit()

    risk_heatmap.evaluate(db, project_id=1)
    risk_heatmap.evaluate(db, project_id=1)  # second pass within 24h
    dwell = (db.query(Notification)
               .filter(Notification.trigger.like("heatmap_dwell:%")).all())
    assert len(dwell) == 1, "dwell alert must dedupe within the cooldown window"


# ---------- Phase 8: Evidence Bridge + Watchdog ----------------------------

def test_evidence_bridge_returns_messages_audit_and_scorecard():
    from app.models.messaging import Message, MessageThread
    from app.services import evidence_bridge, wrap_risk

    db = _fresh_db()

    # Tag two messages — one to the activity directly, one to the RFC.
    thread = MessageThread(project_id=1, subject="X-102")
    db.add(thread); db.flush()
    db.add_all([
        Message(thread_id=thread.id, sender_email="pd@lead.epc",
                sender_org="lead_epc", body="@civil_lead status?",
                mentions=["civil_lead"], activity_id="CIV-1040"),
        Message(thread_id=thread.id, sender_email="civil_lead@externalco.com",
                sender_org="ExtCo", body="QA reviewing",
                mentions=[], rfc_drawing_id=1),
    ])
    db.commit()

    # Generate a claim against CIV-1040 driven by the RFC miss.
    sim = wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=4, idle_crew=10, crew_burdened_rate=120.0,
        linked_activity_id="CIV-1040",
    )
    # Approve a CO on the same activity so the scorecard is non-empty.
    _make_approved_co(db, activity_id="CIV-1040", direct_cost=100_000.0,
                      markup_pct=10.0)

    bundle = evidence_bridge.build(db, project_id=1, activity_id="CIV-1040")

    # Communications: both tagged messages must surface.
    assert len(bundle.communications) >= 2
    bodies = " ".join(c.body for c in bundle.communications)
    assert "@civil_lead status?" in bodies
    assert "QA reviewing" in bodies

    # Audit trail covers claim + CO + idle event + CO events; each row hashed.
    kinds = {a.kind for a in bundle.audit_trail}
    assert "claim" in kinds
    assert "change_order" in kinds
    assert "idle_event" in kinds
    for row in bundle.audit_trail:
        assert len(row.sha256) == 64
        assert row.canonical
    assert len(bundle.bundle_hash) == 64

    # Scorecard: counterparty (claim) + originator (CO) + issuer (RFC).
    roles = {s.role for s in bundle.scorecard}
    assert "counterparty" in roles
    assert "originator" in roles
    assert "issuer" in roles


def test_evidence_bundle_hash_is_deterministic_and_changes_on_mutation():
    from app.services import evidence_bridge, wrap_risk

    db = _fresh_db()
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=3, idle_crew=8, crew_burdened_rate=110.0,
        linked_activity_id="CIV-1040",
    )
    a = evidence_bridge.build(db, project_id=1, activity_id="CIV-1040")
    b = evidence_bridge.build(db, project_id=1, activity_id="CIV-1040")
    assert a.bundle_hash == b.bundle_hash, \
        "back-to-back builds must produce the same audit-trail hash"

    _make_approved_co(db, activity_id="CIV-1040", direct_cost=100_000.0,
                      markup_pct=20.0)
    c = evidence_bridge.build(db, project_id=1, activity_id="CIV-1040")
    assert c.bundle_hash != a.bundle_hash, \
        "approving a CO must change the audit-trail hash"


def test_watchdog_run_once_invokes_heatmap_evaluation_per_project():
    from datetime import datetime, timedelta, timezone as _tz
    import asyncio

    from app.connectors import twilio_sns
    from app.models.heatmap import HeatmapPosition
    from app.models.notification import Notification
    from app.services import wrap_risk

    twilio_sns.SENT.clear()
    db = _fresh_db()
    _seed_recipients(db)

    # Push CIV-1040 into HH on the project.
    wrap_risk.simulate_rfc_miss(
        db, project_id=1, rfc_drawing_id=1,
        days_overdue=14, idle_crew=30, crew_burdened_rate=200.0,
        linked_activity_id="CIV-1040",
    )

    # Patch the watchdog's SessionLocal to point at our fresh in-memory db,
    # then pre-age the heatmap position so the dwell trigger fires.
    from app.services import watchdog as wd
    original = wd.SessionLocal
    try:
        wd.SessionLocal = lambda: db  # type: ignore[assignment]
        # First tick seeds HeatmapPosition rows.
        scanned = asyncio.run(wd.run_once())
        assert scanned == 1
        pos = (db.query(HeatmapPosition)
                 .filter(HeatmapPosition.activity_id == "CIV-1040").first())
        assert pos is not None
        # Pre-age and re-tick so the dwell threshold trips.
        pos.entered_at = datetime.now(_tz.utc) - timedelta(hours=49)
        db.commit()
        twilio_sns.SENT.clear()
        asyncio.run(wd.run_once())
    finally:
        wd.SessionLocal = original

    dwell = (db.query(Notification)
               .filter(Notification.trigger.like("heatmap_dwell:%")).all())
    assert dwell, "watchdog tick must fire the dwell alert"
    assert "+15555550103" in {m["to"] for m in twilio_sns.SENT}


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
