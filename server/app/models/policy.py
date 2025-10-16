# server/app/models/policy.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict
from sqlalchemy import Boolean, DateTime, ForeignKey, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# --------- Pydantic policy used by API ---------

class Policy(BaseModel):
    id: str
    user_id: str = Field(..., exclude=True)
    text: str
    json: Optional[dict[str, Any]] = None
    active: bool = True
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------- ORM policy ---------

class PolicyORM(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    def to_pyd(self) -> Policy:
        return Policy(
            id=self.id,
            user_id=self.user_id,
            text=self.text,
            json=self.json,
            active=self.active,
            created_at=self.created_at,
        )
