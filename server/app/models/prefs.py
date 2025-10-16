# server/app/models/prefs.py
from __future__ import annotations

from typing import Optional
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


# --------- Pydantic prefs used across routes/services ---------

class Prefs(BaseModel):
    sleep_start: str = Field(..., description='24h "HH:MM", e.g., "22:30"')
    sleep_end: str = Field(..., description='24h "HH:MM", e.g., "07:00"')
    min_buffer_min: int = Field(..., ge=0, description="Minimum gap between events")
    default_event_len_min: int = Field(..., ge=5, description="Default event length if not specified")

    model_config = ConfigDict(from_attributes=True)


class PrefsUpdate(BaseModel):
    sleep_start: Optional[str] = None
    sleep_end: Optional[str] = None
    min_buffer_min: Optional[int] = Field(None, ge=0)
    default_event_len_min: Optional[int] = Field(None, ge=5)

    model_config = ConfigDict(from_attributes=True)


# --------- ORM persisted prefs record (per user) ---------

class PrefsORM(Base):
    __tablename__ = "prefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    sleep_start: Mapped[str] = mapped_column(String(8), default="22:30")
    sleep_end: Mapped[str] = mapped_column(String(8), default="07:00")
    min_buffer_min: Mapped[int] = mapped_column(Integer, default=15)
    default_event_len_min: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_pyd(self) -> Prefs:
        return Prefs(
            sleep_start=self.sleep_start,
            sleep_end=self.sleep_end,
            min_buffer_min=self.min_buffer_min,
            default_event_len_min=self.default_event_len_min,
        )
