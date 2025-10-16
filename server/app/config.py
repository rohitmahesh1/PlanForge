# server/app/config.py
from __future__ import annotations

import os
from functools import lru_cache
from pydantic import BaseModel, Field

class Settings(BaseModel):
    # App
    app_name: str = Field(default="assistant-scheduler")
    environment: str = Field(default=os.getenv("ENVIRONMENT", "dev"))
    debug: bool = Field(default=os.getenv("DEBUG", "false").lower() == "true")

    # Auth / Security
    jwt_secret: str = Field(default=os.getenv("JWT_SECRET", "change-me"))
    jwt_issuer: str = Field(default=os.getenv("JWT_ISSUER", "assistant-scheduler"))
    jwt_exp_days: int = Field(default=int(os.getenv("JWT_EXP_DAYS", "30")))
    # Optional passphrase for local token "encryption" fallback when CRYPTO_KEY not set.
    local_secret: str = Field(default=os.getenv("LOCAL_SECRET", "local-dev-only"))

    # Token encryption (optional; if absent, utils.encrypt_token is pass-through)
    crypto_key_b64: str | None = Field(default=os.getenv("CRYPTO_KEY_B64"))

    # Google OAuth
    google_client_id: str = Field(default=os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = Field(default=os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_redirect_uri: str = Field(default=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback"))
    google_scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ]
    )

    # Dev helpers
    allow_debug_header_user: bool = Field(default=os.getenv("ALLOW_DEBUG_HEADER_USER", "true").lower() == "true")

@lru_cache
def get_settings() -> Settings:
    return Settings()
