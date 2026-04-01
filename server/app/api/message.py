# server/app/api/message.py
"""
Message ingress: text & screenshots from Telegram/SMS/web.
The server forwards content (plus prefs/policies/freebusy snapshot) to the LLM,
which decides on tool calls (create/move/update/etc).
"""

from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Planned modules (implement later)
from app.auth.google_oauth import require_user  # returns app.models.user.User
from app.models.user import User
from app.models.prefs import Prefs
from app.models.policy import Policy
from app.services.gcal import GCalClient
from app.services.freebusy import FreeBusyService
from app.services.llm_router import LLMRouter  # orchestrates calls to the model + tool usage
from app.services.policy_store import PolicyStore

router = APIRouter(prefix="/message", tags=["message"])


class MessageIn(BaseModel):
    text: Optional[str] = Field(None, description="User message")
    image_url: Optional[str] = Field(
        None, description="Public URL to screenshot/photo (LLM will OCR)"
    )
    # Optional hint from clients (Telegram/SMS)
    source: Optional[str] = Field(None, description="telegram|sms|web")


class MessageOut(BaseModel):
    status: str
    result_summary: Optional[str] = Field(None, description="Short confirmation for the user")
    # You can include op_ids if the LLM performed writes:
    op_ids: Optional[list[str]] = None


@router.post("", response_model=MessageOut)
async def handle_message(
    payload: MessageIn,
    user: User = Depends(require_user),
) -> MessageOut:
    if not payload.text and not payload.image_url:
        raise HTTPException(status_code=400, detail="Provide text or image_url")

    # Gather minimal context for the LLM
    gcal = GCalClient(user=user)
    prefs: Prefs = await gcal.get_prefs()  # or read from DB when you implement it
    policies: list[Policy] = await PolicyStore(user=user).list_all()

    # A small free/busy snapshot is often helpful for planning
    fb = FreeBusyService(gcal=gcal)
    freebusy_snapshot = await fb.snapshot(hours_ahead=36)

    # Hand off to your LLM router (it will call backend tools/endpoints as needed)
    llm = LLMRouter(user=user)
    result = await llm.process_message(
        text=payload.text,
        image_url=payload.image_url,
        prefs=prefs,
        policies=policies,
        freebusy_snapshot=freebusy_snapshot,
        source=payload.source or "web",
    )

    # Expect the LLM router to return a user-facing summary + any op_ids it executed
    return MessageOut(status="ok", result_summary=result.summary, op_ids=result.op_ids or [])
