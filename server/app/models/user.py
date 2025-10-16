# server/app/models/user.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.alias_generators import to_camel
from pydantic.config import ConfigDict
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# --------- Pydantic representation of the current user (in request context) ---------

class User(BaseModel):
    """
    Lightweight user object injected via auth (e.g., require_user()).
    Backed by the database record but trimmed to non-sensitive fields.
    """
    id: str
    email: Optional[str] = None
    default_calendar_id: Optional[str] = None
    timezone: Optional[str] = Field(None, description="IANA TZ, e.g., 'America/New_York'")

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)


# --------- ORM persisted user (with tokens, etc.) ---------

class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    # Store encrypted refresh token; implement encryption in services.auth when ready.
    google_refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    default_calendar_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    def to_public(self) -> User:
        """Map to a safe, non-sensitive Pydantic user."""
        return User(
            id=self.id,
            email=self.email,
            default_calendar_id=self.default_calendar_id,
            timezone=self.timezone,
        )
