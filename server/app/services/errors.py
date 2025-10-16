# server/app/services/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status


# -----------------------------
# Typed service errors
# -----------------------------

class ServiceError(Exception):
    """Base class for service-layer errors."""
    pass


class OAuthError(ServiceError):
    """OAuth or token exchange/refresh failures."""
    pass


class CalendarError(ServiceError):
    """Generic Google Calendar API error."""
    pass


class NotFoundError(ServiceError):
    """Requested object not found."""
    pass


@dataclass
class ConstraintViolation(ServiceError):
    """
    Raised when a hard scheduling constraint is violated
    (e.g., sleep window, buffer gap, collision).
    """
    reason: str
    detail: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


# -----------------------------
# Helpers to map to HTTP
# -----------------------------

def to_http_exc(err: Exception) -> HTTPException:
    """
    Convert a service error into a consistent HTTPException.
    """
    if isinstance(err, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err) or "Not found")

    if isinstance(err, ConstraintViolation):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "constraint_violation", "reason": err.reason, "detail": err.detail},
        )

    if isinstance(err, OAuthError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OAuth error")

    if isinstance(err, CalendarError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Calendar service error")

    if isinstance(err, ServiceError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err) or "Service error")

    # Fallback
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
