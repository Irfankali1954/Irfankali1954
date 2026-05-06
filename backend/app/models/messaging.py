"""Inter-Company Messaging.

Slack-like threaded messaging that is *context-aware*: a message can be
pinned to a specific :class:`ScheduleActivity`, :class:`RFCDrawing`, or
:class:`PermitStatus` so receivers see the message in the context of the
artifact it concerns. Mentions (``@user``) are extracted into a list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, JSON, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class MessageThread(Base):
    __tablename__ = "message_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", cascade="all,delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("message_threads.id"), index=True)
    sender_email: Mapped[str] = mapped_column(String(255), index=True)
    sender_org: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(String(4000))
    mentions: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Context refs — at most one is non-null per message.
    activity_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    rfc_drawing_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfc_drawings.id"), index=True, nullable=True,
    )
    permit_id: Mapped[int | None] = mapped_column(
        ForeignKey("permit_status.id"), index=True, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    thread: Mapped[MessageThread] = relationship(back_populates="messages")
