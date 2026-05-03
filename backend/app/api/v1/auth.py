from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.user import TokenOut

router = APIRouter()


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(db_session)) -> TokenOut:
    user = db.query(User).filter(User.email == payload.email).one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "inactive user")
    token = create_access_token(
        subject=user.email,
        claims={"role": user.role.value, "org": user.org},
    )
    return TokenOut(access_token=token, role=user.role)
