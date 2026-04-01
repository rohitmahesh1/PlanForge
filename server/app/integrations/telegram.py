# server/app/integrations/telegram.py
"""
Telegram webhook: forwards user text/photos to the LLM pipeline.
Assumes an identity link exists from telegram_chat_id -> internal user_id.

Planned services referenced:
- IdentityLinkService: map Telegram chat/user -> User
- TelegramFileService: convert file_id -> temporary public URL (for OCR)
- FreeBusyService, GCalClient, LLMRouter as in /message flow
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

# Planned imports (implement later)
from app.models.user import User
from app.services.identity import IdentityLinkService  # resolves external ids -> user
from app.services.telegram_files import TelegramFileService  # file_id -> URL
from app.services.ingress_context import IngressContextService
from app.services.llm_router import LLMRouter

router = APIRouter(prefix="/integrations/telegram", tags=["integrations:telegram"])


# ---- Telegram update shapes (minimal subset) ----
class TGPhotoSize(BaseModel):
    file_id: str

class TGMessage(BaseModel):
    message_id: int
    chat: dict
    text: Optional[str] = None
    photo: Optional[list[TGPhotoSize]] = None

class TGUpdate(BaseModel):
    update_id: int
    message: Optional[TGMessage] = None


class TGAck(BaseModel):
    ok: bool = Field(default=True)


@router.post("/webhook", response_model=TGAck)
async def telegram_webhook(req: Request) -> TGAck:
    update = TGUpdate(**(await req.json()))
    if not update.message:
        return TGAck()

    chat = update.message.chat or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return TGAck()

    # Resolve the internal user from Telegram chat id
    identity = IdentityLinkService()
    user: Optional[User] = await identity.get_user_by_telegram_chat(chat_id)
    if not user:
        # You might reply via Telegram API; for now just 401
        raise HTTPException(status_code=401, detail="Unknown Telegram chat. Please link your account.")

    context = await IngressContextService(user=user).build(hours_ahead=36)

    # Text and optional image
    text = update.message.text
    image_url: Optional[str] = None
    if update.message.photo:
        # pick the largest size
        file_id = update.message.photo[-1].file_id
        tgf = TelegramFileService()
        image_url = await tgf.get_public_url(file_id)

    # Delegate to the same LLMRouter used by /message
    llm = LLMRouter(user=user)
    result = await llm.process_message(
        text=text,
        image_url=image_url,
        prefs=context.prefs,
        policies=context.policies,
        freebusy_snapshot=context.freebusy_snapshot,
        source="telegram",
    )

    # (Optionally send a Telegram message back via Bot API here.)
    return TGAck()
