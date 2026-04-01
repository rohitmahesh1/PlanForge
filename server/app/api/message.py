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
from app.services.ingress_context import IngressContextService
from app.services.llm_router import LLMRouter  # orchestrates calls to the model + tool usage

router = APIRouter(prefix="/message", tags=["message"])


class MessageIn(BaseModel):
    text: Optional[str] = Field(None, description="User message")
    image_url: Optional[str] = Field(
        None, description="Public URL to screenshot/photo (LLM will OCR)"
    )
    # Optional hint from clients (Telegram/SMS)
    source: Optional[str] = Field(None, description="telegram|sms|web")
    dry_run: bool = Field(
        default=False,
        description="When true, inspect state and propose actions without making changes",
    )


class MessageOut(BaseModel):
    status: str
    result_summary: Optional[str] = Field(None, description="Short confirmation for the user")
    # You can include op_ids if the LLM performed writes:
    op_ids: Optional[list[str]] = None
    dry_run: bool = False


@router.post("", response_model=MessageOut)
async def handle_message(
    payload: MessageIn,
    user: User = Depends(require_user),
) -> MessageOut:
    if not payload.text and not payload.image_url:
        raise HTTPException(status_code=400, detail="Provide text or image_url")

    # Gather the shared planning context once for the request.
    context = await IngressContextService(user=user).build(hours_ahead=36)

    # Hand off to your LLM router (it will call backend tools/endpoints as needed)
    llm = LLMRouter(user=user)
    result = await llm.process_message(
        text=payload.text,
        image_url=payload.image_url,
        prefs=context.prefs,
        policies=context.policies,
        freebusy_snapshot=context.freebusy_snapshot,
        source=payload.source or "web",
        dry_run=payload.dry_run,
    )

    # Expect the LLM router to return a user-facing summary + any op_ids it executed
    return MessageOut(
        status="ok",
        result_summary=result.summary,
        op_ids=result.op_ids or [],
        dry_run=payload.dry_run,
    )
