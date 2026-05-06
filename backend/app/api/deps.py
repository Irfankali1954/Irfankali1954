"""Reusable FastAPI dependencies — auth, RBAC, DB session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.rbac import DEFAULT_PERMISSIONS, Permission, TechnicalRole
from app.core.security import decode_token
from app.db.session import get_db


@dataclass
class CurrentUser:
    email: str
    role: TechnicalRole
    org: str


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        claims = decode_token(token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))
    return CurrentUser(
        email=claims["sub"],
        role=TechnicalRole(claims.get("role", TechnicalRole.VIEWER.value)),
        org=claims.get("org", "lead_epc"),
    )


def require_permission(perm: Permission):
    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        allowed = DEFAULT_PERMISSIONS.get(user.role, set())
        if perm not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {perm.value}")
        return user
    return _checker


def require_role(*roles: TechnicalRole):
    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {user.role.value} not in {[r.value for r in roles]}")
        return user
    return _checker


def db_session() -> Iterator[Session]:
    yield from get_db()
