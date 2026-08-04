"""User model — the owner of every other entity in the system."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import TimestampMixin, UUIDPKMixin
from app.db.session import Base

if TYPE_CHECKING:
    from app.db.models.token import RefreshToken


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
