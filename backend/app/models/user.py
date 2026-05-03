from sqlalchemy import String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.rbac import TechnicalRole
from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[TechnicalRole] = mapped_column(SAEnum(TechnicalRole))
    org: Mapped[str] = mapped_column(String(128), default="lead_epc")
    is_active: Mapped[bool] = mapped_column(default=True)
