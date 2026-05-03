"""Admin surface — Admin manages the *technology*, not the *visibility*.

Notably, the admin **cannot** rewrite the financial visibility policy. That
power belongs solely to the CFO (see :mod:`app.api.v1.cfo`).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, db_session, require_role
from app.core.rbac import TechnicalRole
from app.core.security import hash_password
from app.models.user import User
from app.schemas.user import UserCreate, UserOut

router = APIRouter()


@router.post(
    "/users",
    response_model=UserOut,
    dependencies=[Depends(require_role(TechnicalRole.ADMIN))],
)
def create_user(payload: UserCreate, db: Session = Depends(db_session)) -> UserOut:
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "email already exists")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        org=payload.org,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.get(
    "/users",
    response_model=list[UserOut],
    dependencies=[Depends(require_role(TechnicalRole.ADMIN))],
)
def list_users(db: Session = Depends(db_session)) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in db.query(User).all()]


@router.get("/whoami", response_model=dict)
def whoami(user: CurrentUser = Depends(require_role(*list(TechnicalRole)))) -> dict:
    return {"email": user.email, "role": user.role.value, "org": user.org}
