# server/app/services/google_oauth.py
from __future__ import annotations

from typing import Dict

from app.services.http import http_json
from app.services.errors import OAuthError


TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


async def exchange_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> Dict:
    """
    Exchange an OAuth `code` for tokens.

    Returns:
      {
        "access_token": "...",
        "expires_in": 3599,
        "refresh_token": "...",  # present on first consent
        "scope": "...",
        "token_type": "Bearer",
        "id_token": "..."        # present when 'openid' in scopes
      }
    """
    try:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        return await http_json("POST", TOKEN_URL, data=data)
    except Exception as exc:  # httpx.HTTPStatusError or transport error
        raise OAuthError(f"Failed to exchange code for tokens: {exc}") from exc


async def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> Dict:
    """
    Use a Google refresh token to obtain a new access token.

    Returns:
      {
        "access_token": "...",
        "expires_in": 3599,
        "scope": "...",
        "token_type": "Bearer"
        # (refresh_token not returned here; you keep the original)
      }
    """
    try:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        return await http_json("POST", TOKEN_URL, data=data)
    except Exception as exc:
        raise OAuthError(f"Failed to refresh access token: {exc}") from exc


async def get_userinfo_email(access_token: str) -> str:
    """
    Retrieve the user's primary email using the access token.
    """
    try:
        data = await http_json(
            "GET",
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        email = (data or {}).get("email")
        if not email:
            raise OAuthError("Google userinfo did not include an email.")
        return email
    except Exception as exc:
        raise OAuthError(f"Failed to fetch Google userinfo: {exc}") from exc
