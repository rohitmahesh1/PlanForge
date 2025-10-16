# server/app/api/ops.py
"""
Operations history & undo.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
from app.models.changelog import ChangeLogEntry
from app.services.undo import ChangeLogger

router = APIRouter(prefix="/ops", tags=["ops"])


class UndoRequest(BaseModel):
    op_id: Optional[str] = Field(
        None, description="If omitted, undo the most recent operation for the user"
    )


class UndoResponse(BaseModel):
    reverted: bool
    restored_event_id: Optional[str] = None


class HistoryItem(BaseModel):
    op_id: str
    type: str
    event_id: Optional[str] = None
    timestamp: datetime


class HistoryResponse(BaseModel):
    items: list[HistoryItem]


@router.post("/undo", response_model=UndoResponse)
async def undo(
    body: UndoRequest,
    user: User = Depends(require_user),
) -> UndoResponse:
    logger = ChangeLogger(user=user)

    if body.op_id:
        ok, restored_event_id = await logger.undo(op_id=body.op_id)
    else:
        ok, restored_event_id = await logger.undo_last()

    if not ok:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    return UndoResponse(reverted=True, restored_event_id=restored_event_id)


@router.get("/history", response_model=HistoryResponse)
async def history(
    limit: int = 20,
    user: User = Depends(require_user),
) -> HistoryResponse:
    logger = ChangeLogger(user=user)
    entries: list[ChangeLogEntry] = await logger.list_recent(limit=limit)
    return HistoryResponse(
        items=[
            HistoryItem(
                op_id=e.op_id,
                type=e.type,
                event_id=e.gcal_event_id,
                timestamp=e.timestamp,
            )
            for e in entries
        ]
    )
