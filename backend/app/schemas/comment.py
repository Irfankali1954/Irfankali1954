from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


CommentTarget = Literal["idle_event", "claim"]


class CommentIn(BaseModel):
    target_kind: CommentTarget
    target_id: int
    body: str = Field(..., min_length=1, max_length=4000)


class CommentOut(BaseModel):
    id: int
    target_kind: str
    target_id: int
    author_email: str
    author_role: str
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}
