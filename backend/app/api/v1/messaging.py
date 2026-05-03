"""Inter-Company Messaging — context-aware Slack-like surface.

A message MAY pin one of: a Gantt activity, an RFC drawing, or a permit.
The receiver's UI renders the pinned artifact alongside the message so the
conversation stays grounded ("we're talking about X-102, not generic delay").

Endpoints
---------

POST /messages                        send (opens new thread if needed)
GET  /messages/threads/{tid}          read full thread
GET  /messages/threads?project_id=…   list threads for a project
GET  /messages/by-activity/{id}       all messages pinned to an activity
GET  /messages/by-rfc/{id}            all messages pinned to an RFC
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, get_current_user
from app.models.messaging import Message, MessageThread
from app.models.risk import PermitStatus, RFCDrawing
from app.models.schedule import ScheduleActivity
from app.schemas.messaging import MessageIn, MessageOut, ThreadOut

router = APIRouter()

_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9._-]+)")


def _extract_mentions(body: str) -> list[str]:
    return list(dict.fromkeys(_MENTION_RE.findall(body)))


def _validate_context(db: Session, payload: MessageIn) -> None:
    ctx = payload.context
    if ctx.type == "activity":
        exists = (
            db.query(ScheduleActivity)
            .filter(
                ScheduleActivity.project_id == payload.project_id,
                ScheduleActivity.activity_id == ctx.activity_id,
            )
            .first()
        )
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "activity not found")
    elif ctx.type == "rfc":
        if db.get(RFCDrawing, ctx.rfc_drawing_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "rfc drawing not found")
    elif ctx.type == "permit":
        if db.get(PermitStatus, ctx.permit_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "permit not found")


@router.post("/", response_model=MessageOut)
def send_message(
    payload: MessageIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(get_current_user),
) -> MessageOut:
    _validate_context(db, payload)

    if payload.thread_id is None:
        if not payload.subject:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "subject required for a new thread")
        thread = MessageThread(project_id=payload.project_id, subject=payload.subject)
        db.add(thread)
        db.flush()
    else:
        thread = db.get(MessageThread, payload.thread_id)
        if thread is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found")

    msg = Message(
        thread_id=thread.id,
        sender_email=user.email,
        sender_org=user.org,
        body=payload.body,
        mentions=_extract_mentions(payload.body),
        activity_id=payload.context.activity_id,
        rfc_drawing_id=payload.context.rfc_drawing_id,
        permit_id=payload.context.permit_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return MessageOut.model_validate(msg)


@router.get("/threads/{thread_id}", response_model=ThreadOut)
def read_thread(
    thread_id: int,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> ThreadOut:
    t = db.get(MessageThread, thread_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found")
    return ThreadOut.model_validate(t)


@router.get("/threads", response_model=list[ThreadOut])
def list_threads(
    project_id: int,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[ThreadOut]:
    rows = (
        db.query(MessageThread)
        .filter(MessageThread.project_id == project_id)
        .order_by(MessageThread.created_at.desc())
        .all()
    )
    return [ThreadOut.model_validate(t) for t in rows]


@router.get("/by-activity/{activity_id}", response_model=list[MessageOut])
def by_activity(
    activity_id: str,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[MessageOut]:
    rows = (
        db.query(Message)
        .filter(Message.activity_id == activity_id)
        .order_by(Message.created_at)
        .all()
    )
    return [MessageOut.model_validate(r) for r in rows]


@router.get("/by-rfc/{rfc_id}", response_model=list[MessageOut])
def by_rfc(
    rfc_id: int,
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[MessageOut]:
    rows = (
        db.query(Message)
        .filter(Message.rfc_drawing_id == rfc_id)
        .order_by(Message.created_at)
        .all()
    )
    return [MessageOut.model_validate(r) for r in rows]
