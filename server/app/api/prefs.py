# server/app/api/prefs.py
"""
User preferences: sleep window, buffers, defaults.
"""

from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user
from app.models.user import User
from app.models.prefs import Prefs, PrefsUpdate
from app.services.gcal import GCalClient  # or a DB-backed PrefsService

router = APIRouter(prefix="/prefs", tags=["prefs"])


class PrefsOut(BaseModel):
    sleep_start: str  # "22:30"
    sleep_end: str    # "07:00"
    min_buffer_min: int
    default_event_len_min: int


@router.get("", response_model=PrefsOut)
async def get_prefs(user: User = Depends(require_user)) -> PrefsOut:
    gcal = GCalClient(user=user)
    prefs: Prefs = await gcal.get_prefs()  # swap to DB when ready
    return PrefsOut(
        sleep_start=prefs.sleep_start,
        sleep_end=prefs.sleep_end,
        min_buffer_min=prefs.min_buffer_min,
        default_event_len_min=prefs.default_event_len_min,
    )


@router.post("", response_model=PrefsOut)
async def update_prefs(
    body: PrefsUpdate,
    user: User = Depends(require_user),
) -> PrefsOut:
    gcal = GCalClient(user=user)
    prefs: Prefs = await gcal.update_prefs(body)  # or call a PrefsService
    return PrefsOut(
        sleep_start=prefs.sleep_start,
        sleep_end=prefs.sleep_end,
        min_buffer_min=prefs.min_buffer_min,
        default_event_len_min=prefs.default_event_len_min,
    )
