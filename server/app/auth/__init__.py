# server/app/auth/__init__.py
from .google_oauth import router, require_user

__all__ = ["router", "require_user"]
