# server/app/services/gcal.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select

from app.config import get_settings
from app.models.base import AsyncSession, get_session
from app.models.user import User, UserORM
from app.models.prefs import Prefs, PrefsUpdate, PrefsORM
from app.services.errors import CalendarError, OAuthError
from app.services.google_oauth import refresh_access_token
from app.services.http import http_json
from app.services.timezone import to_tz
from app.utils import decrypt_token, to_rfc3339, utcnow


GCAL_BASE = "https://www.googleapis.com/calendar/v3"
FREEBUSY_URL = f"{GCAL_BASE}/freeBusy"


class GCalClient:
    """
    Minimal Google Calendar wrapper.

    - Refreshes an access token on demand using the user's refresh token.
    - Provides CRUD wrappers for events.
    - Exposes a DB-backed Prefs interface for MVP.
    """

    def __init__(self, user: User):
        self.user = user
        self._access_token: Optional[str] = None
        self._access_token_expires_at: Optional[datetime] = None

    # -------------------------
    # Auth helpers
    # -------------------------

    async def ensure_access_token(self) -> str:
        """
        Refresh an access token using the user's stored refresh token.
        (MVP: refresh on every call for simplicity.)
        """
        now = utcnow()
        if (
            self._access_token
            and self._access_token_expires_at
            and now < self._access_token_expires_at
        ):
            return self._access_token

        async with get_session() as session:
            # Fetch the persisted user row to access the encrypted refresh token
            user_row = await _get_user_row(session, self.user.id)
            if not user_row or not user_row.google_refresh_token_encrypted:
                raise OAuthError("No refresh token on file. Please reinstall /auth/install.")

            refresh_token = decrypt_token(user_row.google_refresh_token_encrypted)
            s = get_settings()
            tokens = await refresh_access_token(
                refresh_token=refresh_token,
                client_id=s.google_client_id,
                client_secret=s.google_client_secret,
            )
            access_token = tokens.get("access_token")
            if not access_token:
                raise OAuthError("Refresh succeeded but no access_token returned.")
            self._access_token = access_token
            expires_in = _coerce_expires_in(tokens.get("expires_in"))
            if expires_in is not None:
                self._access_token_expires_at = now + timedelta(
                    seconds=max(0, expires_in - 60)
                )
            else:
                self._access_token_expires_at = now + timedelta(minutes=50)
            return access_token

    async def _headers(self) -> Dict[str, str]:
        token = await self.ensure_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_primary_calendar_id(self) -> str:
        """
        Return the user's default calendar id.
        - If a specific default is persisted for the user, use it.
        - Else use "primary" which Google accepts as an alias.
        """
        async with get_session() as session:
            user_row = await _get_user_row(session, self.user.id)
            if user_row and user_row.default_calendar_id:
                return user_row.default_calendar_id
        return "primary"

    # -------------------------
    # Events
    # -------------------------

    async def list_events(
        self,
        start: datetime,
        end: datetime,
        calendar_id: Optional[str] = None,
        *,
        query: Optional[str] = None,
        max_results: int = 2500,
    ) -> list[dict]:
        """
        List single (expanded) events within [start, end).
        """
        cid = calendar_id or await self.get_primary_calendar_id()
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": to_rfc3339(start),
            "timeMax": to_rfc3339(end),
            "maxResults": str(max(1, min(max_results, 2500))),
        }
        if query:
            params["q"] = query
        url = f"{GCAL_BASE}/calendars/{cid}/events"
        try:
            data = await http_json("GET", url, headers=await self._headers(), params=params)
            return data.get("items", [])
        except Exception as exc:
            raise CalendarError(f"Failed to list events: {exc}") from exc

    async def search_events(
        self,
        *,
        query: str,
        start: datetime,
        end: datetime,
        calendar_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        return await self.list_events(
            start,
            end,
            calendar_id=calendar_id,
            query=query,
            max_results=max_results,
        )

    async def get_event(self, event_id: str, calendar_id: Optional[str] = None) -> Optional[dict]:
        cid = calendar_id or await self.get_primary_calendar_id()
        url = f"{GCAL_BASE}/calendars/{cid}/events/{event_id}"
        try:
            # If not found, Google returns 404; let it bubble and convert to None.
            data = await http_json("GET", url, headers=await self._headers())
            return data
        except Exception as exc:
            # A little loose: if 404, return None; otherwise raise.
            if "404" in str(exc):
                return None
            raise CalendarError(f"Failed to get event: {exc}") from exc

    async def create_event(
        self,
        *,
        title: str,
        start: datetime | dict[str, Any],
        end: datetime | dict[str, Any],
        attendees: Optional[list[str]] = None,
        location: Optional[str] = None,
        notes: Optional[str] = None,
        calendar_id: Optional[str] = None,
        priority: Optional[str] = None,  # "high" | "routine"
        private_properties: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Create an event. We store `priority` in private extendedProperties so
        reorg logic can detect routine vs high without parsing titles.
        """
        cid = calendar_id or await self.get_primary_calendar_id()
        tz = self.user.timezone or "UTC"

        body: Dict[str, Any] = {
            "summary": title,
            "start": _normalize_create_time(start, tz),
            "end": _normalize_create_time(end, tz),
        }
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees if a]
        private: Dict[str, Any] = {}
        if priority:
            private["priority"] = priority
        if private_properties:
            private.update({str(k): str(v) for k, v in private_properties.items() if v is not None})
        if private:
            body["extendedProperties"] = {"private": private}

        url = f"{GCAL_BASE}/calendars/{cid}/events"
        try:
            return await http_json("POST", url, headers=await self._headers(), json=body)
        except Exception as exc:
            raise CalendarError(f"Failed to create event: {exc}") from exc

    async def update_event(
        self,
        *,
        event_id: str,
        patch: dict,
        calendar_id: Optional[str] = None,
    ) -> dict:
        """
        Patch an event. `patch` fields correspond to Google Events resource.
        For convenience, if patch contains naive datetimes for start/end,
        it is acceptable to pass {"start": dt, "end": dt2}; they’ll be converted.
        """
        cid = calendar_id or await self.get_primary_calendar_id()
        tz = self.user.timezone or "UTC"

        fixed_patch = _normalize_patch_datetimes(patch, tz)
        url = f"{GCAL_BASE}/calendars/{cid}/events/{event_id}"
        try:
            return await http_json("PATCH", url, headers=await self._headers(), json=fixed_patch)
        except Exception as exc:
            raise CalendarError(f"Failed to update event: {exc}") from exc

    async def delete_event(self, event_id: str, calendar_id: Optional[str] = None) -> None:
        cid = calendar_id or await self.get_primary_calendar_id()
        url = f"{GCAL_BASE}/calendars/{cid}/events/{event_id}"
        try:
            await http_json("DELETE", url, headers=await self._headers())
        except Exception as exc:
            # Deleting a nonexistent id returns 410/404; treat as success for idempotency
            if "404" in str(exc) or "410" in str(exc):
                return
            raise CalendarError(f"Failed to delete event: {exc}") from exc

    # -------------------------
    # Free/Busy
    # -------------------------

    async def freebusy(
        self,
        start: datetime,
        end: datetime,
        calendars: Optional[Iterable[str]] = None,
    ) -> list[dict]:
        """
        Call the FreeBusy API. Returns a list of busy windows across calendars.
        Each item: {"start": "...", "end": "...", "calendar": "<id>"}.
        """
        # Default to the user's primary calendar
        items = [{"id": c} for c in (calendars or [await self.get_primary_calendar_id()])]
        body = {
            "timeMin": to_rfc3339(start),
            "timeMax": to_rfc3339(end),
            "items": items,
        }
        try:
            data = await http_json("POST", FREEBUSY_URL, headers=await self._headers(), json=body)
            res: list[dict] = []
            cals = (data or {}).get("calendars", {})
            for cid, payload in cals.items():
                for b in payload.get("busy", []):
                    res.append({"start": b["start"], "end": b["end"], "calendar": cid})
            return res
        except Exception as exc:
            raise CalendarError(f"Failed to fetch free/busy: {exc}") from exc

    # -------------------------
    # Preferences (DB-backed)
    # -------------------------

    async def get_prefs(self) -> Prefs:
        async with get_session() as session:
            row = await session.execute(select(PrefsORM).where(PrefsORM.user_id == self.user.id))
            obj: Optional[PrefsORM] = row.scalar_one_or_none()
            if obj:
                return obj.to_pyd()
            # If missing, create defaults on the fly (should already exist from /auth/callback)
            obj = PrefsORM(user_id=self.user.id)  # defaults in model
            session.add(obj)
            await session.flush()
            return obj.to_pyd()

    async def update_prefs(self, update: PrefsUpdate) -> Prefs:
        async with get_session() as session:
            row = await session.execute(select(PrefsORM).where(PrefsORM.user_id == self.user.id))
            obj: Optional[PrefsORM] = row.scalar_one_or_none()
            if not obj:
                obj = PrefsORM(user_id=self.user.id)
                session.add(obj)
            # Apply partial update
            if update.sleep_start is not None:
                obj.sleep_start = update.sleep_start
            if update.sleep_end is not None:
                obj.sleep_end = update.sleep_end
            if update.min_buffer_min is not None:
                obj.min_buffer_min = update.min_buffer_min
            if update.default_event_len_min is not None:
                obj.default_event_len_min = update.default_event_len_min
            await session.flush()
            return obj.to_pyd()


# -------------------------
# Internal helpers
# -------------------------

async def _get_user_row(session: AsyncSession, user_id: str) -> Optional[UserORM]:
    row = await session.execute(select(UserORM).where(UserORM.id == user_id))
    return row.scalar_one_or_none()


def _normalize_patch_datetimes(patch: dict, tz_str: str) -> dict:
    """
    Accept friendly patches like {"start": dt, "end": dt2} and convert to GCal format.
    If the caller already supplies Google-style dicts for start/end, pass through.
    """
    fixed = dict(patch or {})
    # Normalize start
    if "start" in fixed and isinstance(fixed["start"], datetime):
        dt = fixed["start"]
        fixed["start"] = {"dateTime": to_rfc3339(to_tz(dt, tz_str)), "timeZone": tz_str}
    # Normalize end
    if "end" in fixed and isinstance(fixed["end"], datetime):
        dt = fixed["end"]
        fixed["end"] = {"dateTime": to_rfc3339(to_tz(dt, tz_str)), "timeZone": tz_str}
    return fixed


def _normalize_create_time(val: datetime | dict[str, Any], tz_str: str) -> dict[str, Any]:
    if isinstance(val, datetime):
        return {"dateTime": to_rfc3339(to_tz(val, tz_str)), "timeZone": tz_str}
    if isinstance(val, dict):
        return dict(val)
    raise TypeError(f"Unsupported event time value: {val!r}")


def _coerce_expires_in(val: Any) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
