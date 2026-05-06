"""Management feedback loop — CEO/CFO/PD notes that site teams see immediately."""

from datetime import datetime, timezone
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ManagementComment(Base):
    """A note from the executive suite anchored to a domain artifact.

    ``target_kind`` is one of ``idle_event`` or ``claim``; ``target_id`` is
    the FK in the corresponding table. Comments are immutable once written
    so the audit trail is defensible.
    """
    __tablename__ = "management_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[int] = mapped_column(index=True)
    author_email: Mapped[str] = mapped_column(String(255))
    author_role: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
