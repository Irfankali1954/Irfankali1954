"""CEO Heatmap — Risk Impact × Net Exposure quadrant classifier.

Each activity that carries either a Delay Claim, an approved Change
Order, or a non-zero Risk Impact gets plotted on the (risk, exposure)
plane and bucketed into one of four quadrants:

    HH — high risk + high exposure  (the wrap-killers)
    HL — high risk + low exposure   (de-risked but still dragging the score)
    LH — low risk  + high exposure  (a write-down waiting to happen)
    LL — low risk  + low exposure   (healthy)

The dwell-time alert
====================

Items in HH are *not allowed* to sit there indefinitely — by definition
they combine schedule risk and financial exposure. If an activity has
been in HH continuously for ≥ 48 hours, we fire a Tier-3 nuclear
notification through the existing Senior Alert List bus (CEO + CFO via
SMS). One alert per 24-hour window per cell so the recipients are not
spammed every cron tick.

The :class:`HeatmapPosition` row carries the timestamps that drive this:
``entered_at`` is reset every time the cell's quadrant changes, so an
activity that briefly leaves HH and returns starts a fresh 48-hour clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.heatmap import HeatmapPosition
from app.services import convergence_service, risk_attribution


HIGH_RISK_THRESHOLD = 5.0          # in score points (out of 100)
HIGH_EXPOSURE_THRESHOLD = 50_000.0  # dollars
DWELL_HOURS_NUCLEAR = 48.0
DWELL_ALERT_COOLDOWN = timedelta(hours=24)


@dataclass
class HeatmapCell:
    activity_id: str
    risk_impact: float
    net_exposure: float
    quadrant: str           # HH | HL | LH | LL
    entered_at: datetime
    hours_in_quadrant: float
    on_critical_path: bool  # convenience copy from convergence
    claim_ids: list[int]
    change_order_ids: list[int]


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _classify(risk: float, exposure: float) -> str:
    h_risk = "H" if risk >= HIGH_RISK_THRESHOLD else "L"
    h_expo = "H" if exposure >= HIGH_EXPOSURE_THRESHOLD else "L"
    return f"{h_risk}{h_expo}"


def evaluate(
    db: Session,
    project_id: int,
    *,
    fire_alerts: bool = True,
    trigger: str = "heatmap",
) -> list[HeatmapCell]:
    """Walk every relevant activity, classify, persist position, fire alerts."""
    attrs = risk_attribution.attribute_for_project(db, project_id)
    expos = convergence_service.compute_for_project(db, project_id)
    expo_by_activity = {e.activity_id: e for e in expos}

    activity_ids = (
        {a.activity_id for a in attrs}
        | {e.activity_id for e in expos}
    )
    cells: list[HeatmapCell] = []
    now = datetime.now(timezone.utc)

    for aid in sorted(activity_ids):
        risk = next((a.risk_impact for a in attrs if a.activity_id == aid), 0.0)
        expo = expo_by_activity.get(aid)
        net = expo.net_exposure if expo else 0.0
        quadrant = _classify(risk, net)

        pos = (
            db.query(HeatmapPosition)
            .filter(
                HeatmapPosition.project_id == project_id,
                HeatmapPosition.activity_id == aid,
            )
            .one_or_none()
        )
        if pos is None:
            pos = HeatmapPosition(
                project_id=project_id, activity_id=aid,
                quadrant=quadrant, risk_impact=risk, net_exposure=net,
                entered_at=now, last_check_at=now,
            )
            db.add(pos)
            db.flush()
        else:
            if pos.quadrant != quadrant:
                pos.quadrant = quadrant
                pos.entered_at = now
            pos.risk_impact = risk
            pos.net_exposure = net
            pos.last_check_at = now

        entered = _aware(pos.entered_at) or now
        hours_in = (now - entered).total_seconds() / 3600.0

        cells.append(HeatmapCell(
            activity_id=aid,
            risk_impact=risk,
            net_exposure=net,
            quadrant=quadrant,
            entered_at=entered,
            hours_in_quadrant=hours_in,
            on_critical_path=expo.double_count_risk if expo else False,
            claim_ids=expo.claim_ids if expo else [],
            change_order_ids=expo.change_order_ids if expo else [],
        ))

        if not fire_alerts:
            continue
        if quadrant != "HH" or hours_in < DWELL_HOURS_NUCLEAR:
            continue
        last_alert = _aware(pos.last_alert_at)
        if last_alert is not None and (now - last_alert) < DWELL_ALERT_COOLDOWN:
            continue
        _fire_dwell_alert(db, project_id=project_id, cell=cells[-1], trigger=trigger)
        pos.last_alert_at = now

    db.commit()
    return cells


# ---------------------------------------------------------------------------
# Dwell-time Tier-3 alert
# ---------------------------------------------------------------------------


def _fire_dwell_alert(
    db: Session,
    *,
    project_id: int,
    cell: HeatmapCell,
    trigger: str,
) -> None:
    """Tier-3 nuclear notification — routes through the existing senior list."""
    from app.models.notification import Notification
    from app.services.notification_service import (
        Tier, _fanout, _recently_fired,
    )

    hour_bucket = int(datetime.now(timezone.utc).timestamp() // 3600)
    key = f"heatmap_hh:{project_id}:{cell.activity_id}:{hour_bucket // 24}"
    if _recently_fired(db, key, within=DWELL_ALERT_COOLDOWN):
        return

    subject = (
        f"[NUCLEAR · HEATMAP] Activity {cell.activity_id} stuck in HH for "
        f"{cell.hours_in_quadrant:.1f}h (risk={cell.risk_impact:.1f} pts · "
        f"exposure=${cell.net_exposure:,.0f})"
    )
    body = (
        f"Project: {project_id}\n"
        f"Activity: {cell.activity_id}\n"
        f"Quadrant: HH (high risk × high exposure)\n"
        f"Risk impact: {cell.risk_impact:.1f} score points\n"
        f"Net exposure: ${cell.net_exposure:,.0f}\n"
        f"Time in quadrant: {cell.hours_in_quadrant:.1f} hours\n"
        f"Claim ids: {', '.join('#' + str(c) for c in cell.claim_ids) or 'none'}\n"
        f"Change Order ids: {', '.join('#' + str(c) for c in cell.change_order_ids) or 'none'}\n"
        "\n** WRAP-KILLER **\n"
        "This activity has carried high schedule-risk drag AND material "
        "financial exposure for more than 48 hours. The CFO and CEO are "
        "being SMS'd because items in this quadrant are the single largest "
        "predictor of COD failure on this contract type.\n"
    )
    notif = Notification(
        project_id=project_id,
        tier=Tier.NUCLEAR.value,
        trigger=f"heatmap_dwell:{trigger}",
        subject=subject,
        body=body,
        cpm_drift_days=0.0,
        open_idle_cost=cell.net_exposure,
        idle_event_id=None,
        claim_id=cell.claim_ids[0] if cell.claim_ids else None,
        dedupe_key=key,
    )
    db.add(notif)
    db.flush()
    notif.dispatched_to = _fanout(db, notif, Tier.NUCLEAR)
    db.commit()
