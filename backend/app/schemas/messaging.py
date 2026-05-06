from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator


ContextType = Literal["activity", "rfc", "permit", "none"]


class MessageContext(BaseModel):
    """Optional pin: ``activity_id``, ``rfc_drawing_id``, or ``permit_id``."""
    type: ContextType = "none"
    activity_id: str | None = None
    rfc_drawing_id: int | None = None
    permit_id: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "MessageContext":
        present = sum(
            1 for x in (self.activity_id, self.rfc_drawing_id, self.permit_id)
            if x is not None
        )
        if self.type == "none" and present != 0:
            raise ValueError("type='none' is incompatible with a context ref")
        if self.type != "none" and present != 1:
            raise ValueError("exactly one context ref required for typed context")
        return self


class MessageIn(BaseModel):
    project_id: int
    thread_id: int | None = None       # if None, a new thread is opened
    subject: str | None = None         # required when opening a new thread
    body: str = Field(..., min_length=1, max_length=4000)
    context: MessageContext = MessageContext()


class MessageOut(BaseModel):
    id: int
    thread_id: int
    sender_email: str
    sender_org: str
    body: str
    mentions: list[str]
    activity_id: str | None
    rfc_drawing_id: int | None
    permit_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ThreadOut(BaseModel):
    id: int
    project_id: int
    subject: str
    created_at: datetime
    messages: list[MessageOut]

    model_config = {"from_attributes": True}
