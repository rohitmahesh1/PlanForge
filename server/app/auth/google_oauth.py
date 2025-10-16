# server/app/auth/google_oauth.py
"""
Google OAuth + session/JWT handling and FastAPI dependency `require_user`.

Design:
- /auth/install -> returns a Google OAuth consent URL
- /auth/callback -> exchanges code for tokens, creates/updates user, issues JWT
- /auth/me -> returns the current user
- require_user -> FastAPI dependency to inject `app.models.user.User`

NOTE: This file references service functions you will implement later
(e.g., `exchange_code_for_tokens`), but includes enough scaffolding to wire up now.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.models.base import get_session, AsyncSession
from app.models.user import User, UserORM
from app.models.prefs import PrefsORM
from app.utils import create_jwt, decode_jwt, encrypt_token, utcnow

# Placeholder imports for planned services you will implement
# - token exchange
# - Google profile lookup (email)
try:
    from app.services.google_oauth import exchange_code_for_tokens, get_userinfo_email
except Exception:  # pragma: no cover
    exchange_code_for_tokens = None  # type: ignore
    get_userinfo_email = None  # type: ignore

router = APIRouter(prefix="/auth", tags=["auth"])


# -----------------------------
# Schemas
# -----------------------------

class InstallOut(BaseModel):
    auth_url: str

class CallbackOut(BaseModel):
    access_token: str = Field(description="JWT for subsequent API calls")
    user: User

class MeOut(BaseModel):
    user: User


# -----------------------------
# Dependency
# -----------------------------

async def require_user(
    authorization: Optional[str] = Header(default=None),
    x_debug_user_id: Optional[str] = Header(default=None),
) -> User:
    """
    Inject the current user. Looks for:
    - Authorization: Bearer <jwt>
    - (dev) X-Debug-User-Id: <user-id> (if ALLOW_DEBUG_HEADER_USER=true)
    """
    settings = get_settings()

    # Dev shortcut header
    if settings.allow_debug_header_user and x_debug_user_id:
        async with get_session() as session:
            user = await _fetch_user_public(session, x_debug_user_id)
            if user:
                return user
        raise HTTPException(status_code=401, detail="Unknown debug user id")

    # Bearer token
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    async with get_session() as session:
        user = await _fetch_user_public(session, payload.sub)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user


async def _fetch_user_public(session: AsyncSession, user_id: str) -> Optional[User]:
    row = await session.execute(select(UserORM).where(UserORM.id == user_id))
    obj: Optional[UserORM] = row.scalar_one_or_none()
    return obj.to_public() if obj else None


# -----------------------------
# Routes
# -----------------------------

@router.get("/install", response_model=InstallOut)
async def auth_install() -> InstallOut:
    """
    Returns the Google OAuth consent URL.
    Client should redirect the user to this URL.
    """
    s = get_settings()
    params = {
        "response_type": "code",
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "scope": " ".join(s.google_scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return InstallOut(auth_url=url)


@router.get("/callback", response_model=CallbackOut)
async def auth_callback(code: str) -> CallbackOut:
    """
    Exchange `code` for tokens, upsert user, create default prefs if missing,
    store encrypted refresh token, and issue a JWT for API calls.
    """
    if not exchange_code_for_tokens:
        raise HTTPException(status_code=500, detail="OAuth exchange service not implemented yet")

    s = get_settings()
    # 1) Exchange code for tokens (you will implement this service)
    tokens = await exchange_code_for_tokens(
        code=code,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        redirect_uri=s.google_redirect_uri,
    )
    # tokens: { access_token, refresh_token, id_token, expires_in, ... }

    # 2) Get email from Google userinfo
    if not get_userinfo_email:
        raise HTTPException(status_code=500, detail="Userinfo service not implemented yet")
    email = await get_userinfo_email(tokens.get("access_token"))

    # 3) Upsert user
    async with get_session() as session:
        user = await _upsert_user_with_tokens(session, email=email, refresh_token=tokens.get("refresh_token"))

        # Ensure prefs row exists (defaults)
        await _ensure_default_prefs(session, user_id=user.id)

    # 4) Issue JWT
    jwt_token = create_jwt(user.id)

    return CallbackOut(access_token=jwt_token, user=user)


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(require_user)) -> MeOut:
    return MeOut(user=user)


# -----------------------------
# Persistence helpers
# -----------------------------

async def _upsert_user_with_tokens(session: AsyncSession, email: str, refresh_token: Optional[str]) -> User:
    # Check if user exists
    row = await session.execute(select(UserORM).where(UserORM.email == email))
    existing: Optional[UserORM] = row.scalar_one_or_none()
    if existing:
        if refresh_token:
            existing.google_refresh_token_encrypted = encrypt_token(refresh_token)
        # keep existing.default_calendar_id, timezone as they may be set later
        await session.flush()
        return existing.to_public()

    # Create new user
    obj = UserORM(
        email=email,
        google_refresh_token_encrypted=encrypt_token(refresh_token) if refresh_token else None,
        default_calendar_id=None,
        timezone=None,
    )
    session.add(obj)
    await session.flush()
    return obj.to_public()

async def _ensure_default_prefs(session: AsyncSession, user_id: str) -> None:
    row = await session.execute(select(PrefsORM).where(PrefsORM.user_id == user_id))
    pref: Optional[PrefsORM] = row.scalar_one_or_none()
    if pref:
        return
    session.add(PrefsORM(user_id=user_id))  # defaults: 22:30–07:00, buffer 15, default len 30
    await session.flush()
