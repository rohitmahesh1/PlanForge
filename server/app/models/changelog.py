# server/app/models/changelog.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict
from sqlalchemy import DateTime, ForeignKey, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class OperationType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


# --------- Pydantic changelog entry (used by ops/history & services.undo) ---------

class ChangeLogEntry(BaseModel):
    op_id: str
    user_id: str = Field(..., exclude=True)
    type: OperationType
    gcal_event_id: Optional[str] = None
    before_json: Optional[dict[str, Any]] = None
    after_json: Optional[dict[str, Any]] = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


# --------- ORM changelog ---------

class ChangeLogORM(Base):
    __tablename__ = "changelog"

    op_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))  # store OperationType.value
    gcal_event_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    before_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    def to_pyd(self) -> ChangeLogEntry:
        return ChangeLogEntry(
            op_id=self.op_id,
            user_id=self.user_id,
            type=OperationType(self.type),
            gcal_event_id=self.gcal_event_id,
            before_json=self.before_json,
            after_json=self.after_json,
            timestamp=self.timestamp,
        )
