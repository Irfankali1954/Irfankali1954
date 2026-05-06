"""Silo-Buster Notification Bus.

Three-tier urgency model:

* **Tier 1 — Normal:** dashboard row only.
* **Tier 2 — Urgent:** dashboard + email to anyone whose recipient row
  carries ``tier_2`` (typically Project Director, CFO).
* **Tier 3 — Nuclear:** dashboard + tier-2 email + SMS to every
  CFO-curated recipient with ``tier_3`` in their tier list. This is the
  *"text the CEO"* path.

Tier-resolution inputs
======================

The service combines two CPM-derived signals with idle-cost telemetry:

* ``cpm_drift_days`` — Δ between the latest CriticalPathSnapshot's
  ``project_finish`` and either (a) the immediately prior snapshot, or
  (b) the contractual ``Project.cod_target`` if no prior snapshot exists.
  Always clamped to ≥ 0; the bus does not celebrate good news.
* ``open_idle_cost`` — Σ ``computed_cost`` over IdleEvents whose
  ``ended_at`` is null (still bleeding).

Tier escalation matrix (defaults; CFO can tune via API later)::

                    drift_days     OR    open_idle_cost
    Tier 3 nuclear  >= 5.0 days    OR    >= $50,000
    Tier 2 urgent   >= 2.0 days    (cost only contributes to nuclear)
    Tier 1 normal   otherwise

Dedupe
======

A single drift event would otherwise re-fire on every CPM recompute. Each
notification carries a ``dedupe_key`` of the form::

    f"{project_id}:{tier}:{drift_bucket}:{cost_bucket}"

where buckets quantize drift to whole days and cost to $10k slabs. We
suppress any new notification matching a key that already fired in the
last hour.

This module is intentionally pure-functional with respect to messaging:
the email + SMS adapters are capture-mode by default
(:mod:`app.connectors.email_smtp`, :mod:`app.connectors.twilio_sns`) so
tests assert the dispatch payload without touching real providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.connectors import email_smtp, twilio_sns
from app.models.financial import Project
from app.models.notification import Notification, NotificationRecipient
from app.models.risk import IdleEvent
from app.models.schedule import CriticalPathSnapshot


class Tier(str, Enum):
    NORMAL = "tier_1"
    URGENT = "tier_2"
    NUCLEAR = "tier_3"


@dataclass(frozen=True)
class TierThresholds:
    urgent_drift_days: float = 2.0
    nuclear_drift_days: float = 5.0
    nuclear_idle_cost: float = 50_000.0


@dataclass
class EvalContext:
    project_id: int
    cpm_drift_days: float
    open_idle_cost: float
    trigger: str
    idle_event_id: int | None = None
    claim_id: int | None = None
    cod_target: datetime | None = None
    projected_finish: datetime | None = None


# ---------------------------------------------------------------------------
# Tier resolution — the heart of "text the CEO?"
# ---------------------------------------------------------------------------


def resolve_tier(ctx: EvalContext, t: TierThresholds = TierThresholds()) -> Tier:
    if ctx.cpm_drift_days >= t.nuclear_drift_days or ctx.open_idle_cost >= t.nuclear_idle_cost:
        return Tier.NUCLEAR
    if ctx.cpm_drift_days >= t.urgent_drift_days:
        return Tier.URGENT
    return Tier.NORMAL


# ---------------------------------------------------------------------------
# CPM integration
# ---------------------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cpm_drift_days(db: Session, project_id: int) -> tuple[float, datetime | None, datetime | None]:
    """Return ``(drift_days, projected_finish, cod_target)``.

    Strategy:
      1. If ≥2 CPM snapshots exist, drift = latest.finish − previous.finish.
         This catches step changes from the most recent recompute, which is
         what triggered us in the first place.
      2. Otherwise, drift = latest.finish − project.cod_target.
    """
    latest = (
        db.query(CriticalPathSnapshot)
        .filter(CriticalPathSnapshot.project_id == project_id)
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    if latest is None:
        return 0.0, None, None
    prior = (
        db.query(CriticalPathSnapshot)
        .filter(
            CriticalPathSnapshot.project_id == project_id,
            CriticalPathSnapshot.id != latest.id,
        )
        .order_by(CriticalPathSnapshot.computed_at.desc())
        .first()
    )
    finish = _aware(latest.project_finish)
    if prior is not None:
        prior_finish = _aware(prior.project_finish)
        if finish and prior_finish:
            return max(0.0, (finish - prior_finish).total_seconds() / 86_400.0), finish, None
    project = db.get(Project, project_id)
    target = _aware(project.cod_target) if project else None
    if finish and target:
        return max(0.0, (finish - target).total_seconds() / 86_400.0), finish, target
    return 0.0, finish, target


def open_idle_cost(db: Session, project_id: int) -> float:
    return float(sum(
        float(e.computed_cost or 0)
        for e in db.query(IdleEvent)
        .filter(IdleEvent.project_id == project_id, IdleEvent.ended_at.is_(None))
        .all()
    ))


# ---------------------------------------------------------------------------
# Dedupe + persistence + fan-out
# ---------------------------------------------------------------------------


def _dedupe_key(ctx: EvalContext, tier: Tier) -> str:
    drift_bucket = int(ctx.cpm_drift_days)
    cost_bucket = int(ctx.open_idle_cost / 10_000.0)  # $10k slabs
    return f"{ctx.project_id}:{tier.value}:{drift_bucket}:{cost_bucket}"


def _recently_fired(db: Session, key: str, *, within: timedelta = timedelta(hours=1)) -> bool:
    cutoff = datetime.now(timezone.utc) - within
    return (
        db.query(Notification)
        .filter(Notification.dedupe_key == key, Notification.created_at >= cutoff)
        .first()
        is not None
    )


def _build_message(ctx: EvalContext, tier: Tier) -> tuple[str, str]:
    finish_s = ctx.projected_finish.strftime("%Y-%m-%d") if ctx.projected_finish else "n/a"
    target_s = ctx.cod_target.strftime("%Y-%m-%d") if ctx.cod_target else "n/a"
    head = {
        Tier.NORMAL: "[NOTICE]",
        Tier.URGENT: "[URGENT]",
        Tier.NUCLEAR: "[NUCLEAR]",
    }[tier]
    subject = f"{head} Project #{ctx.project_id} — CPM drift {ctx.cpm_drift_days:.1f}d"
    body = (
        f"Trigger: {ctx.trigger}\n"
        f"Project: {ctx.project_id}\n"
        f"Critical-path drift: {ctx.cpm_drift_days:.1f} days "
        f"(projected finish {finish_s}, target {target_s})\n"
        f"Open idle cost (uncovered): ${ctx.open_idle_cost:,.0f}\n"
    )
    if ctx.claim_id:
        body += f"Auto-drafted Delay Claim: #{ctx.claim_id}\n"
    if ctx.idle_event_id:
        body += f"Linked Idle Event: #{ctx.idle_event_id}\n"
    return subject, body


def _fanout(db: Session, notif: Notification, tier: Tier) -> list[dict]:
    """Send through email/SMS adapters and record per-channel results.

    Tier 1 → no fan-out (dashboard only).
    Tier 2 → email all recipients with 'tier_2' in their tier list.
    Tier 3 → email tier-2 recipients **and** SMS every active recipient
             whose tier list contains 'tier_3'.
    """
    if tier is Tier.NORMAL:
        return []
    dispatched: list[dict] = []
    recipients = (
        db.query(NotificationRecipient)
        .filter(NotificationRecipient.active.is_(True))
        .all()
    )

    def _email(targets):
        for r in targets:
            if not r.email:
                continue
            res = email_smtp.send(to=r.email, subject=notif.subject, body=notif.body)
            dispatched.append({
                "channel": "email", "to": r.email, "name": r.name,
                "tier": tier.value, "ok": res.ok, "error": res.error,
            })

    def _sms(targets):
        for r in targets:
            if not r.phone:
                continue
            res = twilio_sns.send(to=r.phone, body=f"{notif.subject}\n{notif.body}")
            dispatched.append({
                "channel": "sms", "to": r.phone, "name": r.name,
                "tier": tier.value, "ok": res.ok, "sid": res.sid, "error": res.error,
            })

    tier2 = [r for r in recipients if "tier_2" in (r.tiers or [])]
    tier3 = [r for r in recipients if "tier_3" in (r.tiers or [])]

    if tier in (Tier.URGENT, Tier.NUCLEAR):
        _email(tier2)
    if tier is Tier.NUCLEAR:
        _sms(tier3)

    return dispatched


def evaluate_change_order_alert(
    db: Session,
    *,
    change_order_id: int,
    deadline_kind: str,         # "notice" | "claim"
    severity: str,              # "approaching" | "missed"
    seconds_remaining: float,
    on_critical_path: bool,
    trigger: str = "sentinel",
) -> Notification | None:
    """Aging-clock notification path for the Change Order Sentinel.

    Tier mapping is *intentionally stricter* than the generic CPM-drift
    bus, because a missed time-bar permanently forfeits recovery rights:

    * ``missed``                   → **Tier 3** (text the CEO).
    * ``approaching`` on critical path → **Tier 3** (a missed CO on the
      critical path is a wrap-killer; we do not let it slip).
    * ``approaching`` off critical path → **Tier 2** (email PD/CFO).

    Dedupe key is ``co:{id}:{kind}:{severity}:hour-bucket`` so a 7-day
    aging window doesn't spam — one alert per hour per state change.
    """
    from app.models.change_order import ChangeOrder  # avoid circular import

    co = db.get(ChangeOrder, change_order_id)
    if co is None:
        return None

    if severity == "missed":
        tier = Tier.NUCLEAR
    elif severity == "approaching" and on_critical_path:
        tier = Tier.NUCLEAR
    elif severity == "approaching":
        tier = Tier.URGENT
    else:
        return None

    # Hour-bucket dedupe so we don't re-alert every minute on the same state.
    hour_bucket = int(datetime.now(timezone.utc).timestamp() // 3600)
    key = f"co:{co.id}:{deadline_kind}:{severity}:{hour_bucket}"
    if _recently_fired(db, key, within=timedelta(hours=1)):
        return None

    days_left = max(0.0, seconds_remaining / 86_400.0)
    label = "TIME BAR" if severity == "missed" else "AGING"
    cp_tag = " · CRITICAL PATH" if on_critical_path else ""
    subject = (
        f"[{tier.value.upper()} · {label}] CO {co.co_number} — "
        f"{deadline_kind} deadline {'missed' if severity == 'missed' else f'in {days_left:.1f}d'}"
        f"{cp_tag}"
    )
    body = (
        f"Change Order: {co.co_number} — {co.title}\n"
        f"Linked activity: {co.linked_activity_id} (critical={on_critical_path})\n"
        f"Originator: {co.originator_org}\n"
        f"Status: {co.status}\n"
        f"{deadline_kind.title()} due: {co.notice_due_by if deadline_kind == 'notice' else co.claim_due_by}\n"
        f"Severity: {severity}\n"
    )
    if severity == "missed":
        body += (
            "\n** TIME BAR BREACH **\n"
            f"The {deadline_kind} deadline has passed without action. Under the "
            "master agreement this may forfeit the contractor's right to "
            "recovery on this change. Confirm with counsel immediately.\n"
        )

    notif = Notification(
        project_id=co.project_id,
        tier=tier.value,
        trigger=f"co_aging:{trigger}",
        subject=subject,
        body=body,
        cpm_drift_days=days_left,
        open_idle_cost=0.0,
        idle_event_id=None,
        claim_id=None,
        dedupe_key=key,
    )
    db.add(notif)
    db.flush()
    notif.dispatched_to = _fanout(db, notif, tier)
    db.commit()
    db.refresh(notif)
    return notif


def evaluate_for_project(
    db: Session,
    project_id: int,
    *,
    trigger: str,
    idle_event_id: int | None = None,
    claim_id: int | None = None,
    thresholds: TierThresholds = TierThresholds(),
) -> Notification | None:
    """Run the full pipeline. Returns the persisted Notification, or None
    when dedupe suppressed an otherwise-duplicate alert."""
    drift, finish, target = cpm_drift_days(db, project_id)
    cost = open_idle_cost(db, project_id)
    ctx = EvalContext(
        project_id=project_id,
        cpm_drift_days=drift,
        open_idle_cost=cost,
        trigger=trigger,
        idle_event_id=idle_event_id,
        claim_id=claim_id,
        cod_target=target,
        projected_finish=finish,
    )
    tier = resolve_tier(ctx, thresholds)
    key = _dedupe_key(ctx, tier)
    if _recently_fired(db, key):
        return None

    subject, body = _build_message(ctx, tier)
    notif = Notification(
        project_id=project_id,
        tier=tier.value,
        trigger=trigger,
        subject=subject,
        body=body,
        cpm_drift_days=drift,
        open_idle_cost=cost,
        idle_event_id=idle_event_id,
        claim_id=claim_id,
        dedupe_key=key,
    )
    db.add(notif)
    db.flush()
    notif.dispatched_to = _fanout(db, notif, tier)
    db.commit()
    db.refresh(notif)
    return notif
