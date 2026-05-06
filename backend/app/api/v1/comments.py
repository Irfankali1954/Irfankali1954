"""Management comments — CEO/CFO/PD notes that surface on field artifacts.

Comments are immutable once written; the Statement of Facts and downstream
audit trails rely on this property.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, get_current_user, require_permission
from app.core.rbac import Permission
from app.models.comment import ManagementComment
from app.models.risk import DelayClaim, IdleEvent
from app.schemas.comment import CommentIn, CommentOut

router = APIRouter()


def _verify_target(db: Session, kind: str, tid: int) -> None:
    if kind == "idle_event":
        if db.get(IdleEvent, tid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "idle event not found")
    elif kind == "claim":
        if db.get(DelayClaim, tid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown target_kind: {kind}")


@router.post(
    "",
    response_model=CommentOut,
    dependencies=[Depends(require_permission(Permission.MGMT_COMMENT_WRITE))],
)
def post_comment(
    payload: CommentIn,
    db: Session = Depends(db_session),
    user: CurrentUser = Depends(require_permission(Permission.MGMT_COMMENT_WRITE)),
) -> CommentOut:
    _verify_target(db, payload.target_kind, payload.target_id)
    row = ManagementComment(
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        author_email=user.email,
        author_role=user.role.value,
        body=payload.body,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return CommentOut.model_validate(row)


@router.get("", response_model=list[CommentOut])
def list_comments(
    target_kind: str = Query(...),
    target_id: int = Query(...),
    db: Session = Depends(db_session),
    _: CurrentUser = Depends(get_current_user),
) -> list[CommentOut]:
    rows = (
        db.query(ManagementComment)
        .filter(
            ManagementComment.target_kind == target_kind,
            ManagementComment.target_id == target_id,
        )
        .order_by(ManagementComment.created_at)
        .all()
    )
    return [CommentOut.model_validate(r) for r in rows]
