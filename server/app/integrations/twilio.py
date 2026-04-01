# server/app/integrations/twilio.py
"""
Twilio SMS/MMS webhook: forwards text/media to the LLM pipeline.
Assumes an identity link exists from phone_number -> internal user_id.

Planned services referenced:
- IdentityLinkService: map E.164 number -> User
- TwilioMediaService: normalize MediaUrl0 -> public URL (optional)
- FreeBusyService, GCalClient, LLMRouter as in /message flow

NOTE: For production, verify X-Twilio-Signature header. A TwilioValidationService
can be added later to check the signature using your auth token.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from pydantic import BaseModel

# Planned imports (implement later)
from app.models.user import User
from app.services.identity import IdentityLinkService
from app.services.twilio_media import TwilioMediaService
from app.services.ingress_context import IngressContextService
from app.services.llm_router import LLMRouter

router = APIRouter(prefix="/integrations/twilio", tags=["integrations:twilio"])


class TwilioAck(BaseModel):
    ok: bool = True


@router.post("/webhook", response_model=TwilioAck)
async def twilio_webhook(
    request: Request,
    From: str = Form(...),            # E.164 number
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: Optional[str] = Form(None),
) -> TwilioAck:
    # TODO: verify signature in X-Twilio-Signature using your auth token
    identity = IdentityLinkService()
    user: Optional[User] = await identity.get_user_by_phone(From)
    if not user:
        raise HTTPException(status_code=401, detail="Unknown phone number. Please link your account.")

    context = await IngressContextService(user=user).build(hours_ahead=36)

    # Handle optional media
    image_url: Optional[str] = None
    if NumMedia and NumMedia != "0" and MediaUrl0:
        tms = TwilioMediaService()
        image_url = await tms.normalize_url(MediaUrl0)

    llm = LLMRouter(user=user)
    result = await llm.process_message(
        text=Body or None,
        image_url=image_url,
        prefs=context.prefs,
        policies=context.policies,
        freebusy_snapshot=context.freebusy_snapshot,
        source="sms",
    )
    return TwilioAck()
