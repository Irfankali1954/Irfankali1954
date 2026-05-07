"""First-boot admin bootstrap.

Runs once during the FastAPI lifespan. If ``BOOTSTRAP_ADMIN_EMAIL`` +
``BOOTSTRAP_ADMIN_PASSWORD`` are set in the environment AND no user with
that email exists yet, the bootstrap creates an admin user. Idempotent —
safe to run on every boot.

Production deployments typically set these only on first deploy and unset
them afterwards. For demos / dev, leaving them in ``.env`` is fine.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.rbac import TechnicalRole
from app.core.security import hash_password
from app.models.user import User

log = logging.getLogger(__name__)


def bootstrap_admin(db: Session, settings: Settings) -> User | None:
    """Create the bootstrap admin if its env vars are set and the user is
    missing. Returns the User row when something was created or already
    existed, or ``None`` if the bootstrap is disabled."""
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if not email or not password:
        log.info("bootstrap_admin: disabled (BOOTSTRAP_ADMIN_EMAIL/PASSWORD unset)")
        return None

    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is not None:
        log.info("bootstrap_admin: %s already exists", email)
        return existing

    user = User(
        email=email,
        full_name=settings.bootstrap_admin_name,
        hashed_password=hash_password(password),
        role=TechnicalRole.ADMIN,
        org="lead_epc",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("bootstrap_admin: created %s (role=admin)", email)
    return user
