"""Notification + Senior Alert List API.

* GET  /notifications?project_id=…           — dashboard feed
* POST /notifications/evaluate               — manual re-evaluate (admin/CFO/PD)
* GET  /notifications/recipients             — view senior alert list
* PUT  /notifications/recipients             — replace list (CFO-only)
* POST /notifications/recipients             — add/update single (CFO-only)
* DEL  /notifications/recipients/{id}        — deactivate (CFO-only)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, get_current_user, require_permission
from app.core.rbac import Permission
from app.models.notification import Notification, NotificationRecipient
from app.schemas.notification import (
    EvaluateIn,
    NotificationOut,
    RecipientIn,
    RecipientOut,
)
from app.services import notification_service

router = APIRouter()


@router.get("", response_model=list[NotificationOut])
def feed(
    project_id: int = Query(...),
    limit: int = Query(50, le=200),
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[NotificationOut]:
    rows = (
        db.query(Notification)
        .filter(Notification.project_id == project_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )
    return [NotificationOut.model_validate(r) for r in rows]


@router.post("/evaluate", response_model=NotificationOut | None)
def evaluate(
    payload: EvaluateIn,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
):
    n = notification_service.evaluate_for_project(
        db, payload.project_id, trigger=payload.trigger,
    )
    return NotificationOut.model_validate(n) if n else None


# --- Senior alert list (CFO-managed) ---------------------------------------


@router.get("/recipients", response_model=list[RecipientOut])
def list_recipients(
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[RecipientOut]:
    rows = db.query(NotificationRecipient).order_by(NotificationRecipient.id).all()
    return [RecipientOut.model_validate(r) for r in rows]


@router.put(
    "/recipients",
    response_model=list[RecipientOut],
    dependencies=[Depends(require_permission(Permission.SENIOR_ALERT_LIST_WRITE))],
)
def replace_recipients(
    rows: list[RecipientIn],
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.SENIOR_ALERT_LIST_WRITE)),
) -> list[RecipientOut]:
    """Replace the full senior alert list. CFO-only.

    Sender of every Tier-3 SMS comes from this list. Use sparingly — these
    are the people whose phones light up at 2am when COD is in jeopardy.
    """
    db.query(NotificationRecipient).delete()
    db.flush()
    out: list[NotificationRecipient] = []
    now = datetime.now(timezone.utc)
    for r in rows:
        rec = NotificationRecipient(
            name=r.name, role_label=r.role_label,
            email=r.email, phone=r.phone,
            tiers=list(r.tiers), active=r.active,
            updated_by=user.email, updated_at=now,
        )
        db.add(rec)
        out.append(rec)
    db.commit()
    for rec in out:
        db.refresh(rec)
    return [RecipientOut.model_validate(r) for r in out]


@router.post(
    "/recipients",
    response_model=RecipientOut,
    dependencies=[Depends(require_permission(Permission.SENIOR_ALERT_LIST_WRITE))],
)
def upsert_recipient(
    payload: RecipientIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.SENIOR_ALERT_LIST_WRITE)),
) -> RecipientOut:
    rec = NotificationRecipient(
        name=payload.name, role_label=payload.role_label,
        email=payload.email, phone=payload.phone,
        tiers=list(payload.tiers), active=payload.active,
        updated_by=user.email, updated_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return RecipientOut.model_validate(rec)


@router.delete(
    "/recipients/{recipient_id}",
    dependencies=[Depends(require_permission(Permission.SENIOR_ALERT_LIST_WRITE))],
)
def deactivate(
    recipient_id: int,
    db: Session = Depends(db_session),
) -> dict:
    rec = db.get(NotificationRecipient, recipient_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipient not found")
    rec.active = False
    db.commit()
    return {"id": rec.id, "active": rec.active}
