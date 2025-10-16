# server/app/utils.py
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel
from .config import get_settings

# Optional Fernet token encryption
try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore
    _HAS_CRYPTO = False

# Optional JWT
try:
    import jwt  # PyJWT
    _HAS_JWT = True
except Exception:  # pragma: no cover
    jwt = None  # type: ignore
    _HAS_JWT = False


# -------------------------
# Time & ID helpers
# -------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def from_rfc3339(s: str) -> datetime:
    # Accepts "YYYY-MM-DDTHH:MM:SS+00:00" style strings
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def gen_id() -> str:
    return str(uuid.uuid4())

def make_op_id() -> str:
    return gen_id()


# -------------------------
# Token "encryption"
# -------------------------

def _get_fernet() -> Optional[Fernet]:
    settings = get_settings()
    if not _HAS_CRYPTO:
        return None
    key_b64 = settings.crypto_key_b64
    if not key_b64:
        return None
    try:
        key = key_b64.encode("utf-8")
        return Fernet(key)  # type: ignore[arg-type]
    except Exception:
        return None

def encrypt_token(plain: str) -> str:
    """
    Encrypt a sensitive token at rest.
    - If CRYPTO_KEY_B64 is set and cryptography is installed, use Fernet.
    - Otherwise, fallback to base64 with a local secret (NOT secure; for dev only).
    """
    if not plain:
        return plain
    f = _get_fernet()
    if f:
        return f.encrypt(plain.encode("utf-8")).decode("utf-8")
    # Dev fallback: xor with local secret then base64
    secret = get_settings().local_secret.encode("utf-8")
    raw = bytes([c ^ secret[i % len(secret)] for i, c in enumerate(plain.encode("utf-8"))])
    return base64.urlsafe_b64encode(raw).decode("utf-8")

def decrypt_token(cipher: str) -> str:
    if not cipher:
        return cipher
    f = _get_fernet()
    if f:
        return f.decrypt(cipher.encode("utf-8")).decode("utf-8")
    secret = get_settings().local_secret.encode("utf-8")
    try:
        raw = base64.urlsafe_b64decode(cipher.encode("utf-8"))
        plain = bytes([c ^ secret[i % len(secret)] for i, c in enumerate(raw)])
        return plain.decode("utf-8")
    except Exception:
        # If decode fails, just return input (dev fallback)
        return cipher


# -------------------------
# JWT helpers
# -------------------------

class JWTPayload(BaseModel):
    sub: str  # user_id
    iss: str
    iat: int
    exp: int

def create_jwt(user_id: str, exp_days: Optional[int] = None) -> str:
    """
    Create a signed JWT for client auth.
    """
    settings = get_settings()
    if not _HAS_JWT:
        # Dev fallback: unsigned token (DO NOT USE IN PROD)
        payload = {
            "sub": user_id,
            "iss": settings.jwt_issuer,
            "iat": int(utcnow().timestamp()),
            "exp": int((utcnow() + timedelta(days=exp_days or settings.jwt_exp_days)).timestamp()),
            "alg": "none",
        }
        return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    now = utcnow()
    payload = {
        "sub": user_id,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=exp_days or settings.jwt_exp_days)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token

def decode_jwt(token: str) -> JWTPayload | None:
    settings = get_settings()
    if not token:
        return None
    if _HAS_JWT:
        try:
            data = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)
            return JWTPayload(**data)
        except Exception:
            return None
    # Dev fallback: try to base64 decode
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8"))
        return JWTPayload(**data)
    except Exception:
        return None


# -------------------------
# Misc helpers
# -------------------------

def pick_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "y", "on")

def coalesce(*vals: Any, default: Any = None) -> Any:
    for v in vals:
        if v is not None:
            return v
    return default
