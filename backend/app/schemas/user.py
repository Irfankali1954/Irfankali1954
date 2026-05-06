from pydantic import BaseModel, EmailStr

from app.core.rbac import TechnicalRole


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: TechnicalRole
    org: str = "lead_epc"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: TechnicalRole
    org: str
    is_active: bool

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: TechnicalRole
