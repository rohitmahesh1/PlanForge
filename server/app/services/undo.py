# server/app/services/undo.py
from __future__ import annotations

from typing import Optional, Tuple, Any, Dict, List

from sqlalchemy import select, desc

from app.models.base import AsyncSession, get_session
from app.models.changelog import ChangeLogEntry, ChangeLogORM, OperationType
from app.models.user import User
from app.services.errors import NotFoundError, ServiceError
from app.services.gcal import GCalClient


class ChangeLogger:
    """
    Centralized change logging + undo support.

    We store the full Google Event JSON (before/after) for each write.
    Undo applies the inverse operation WITHOUT writing a new changelog entry.
    """

    def __init__(self, user: User):
        self.user = user

    # ---------------------------
    # Record operations
    # ---------------------------

    async def record_create(self, *, after_json: dict) -> ChangeLogEntry:
        """Log a CREATE operation."""
        async with get_session() as session:
            row = ChangeLogORM(
                user_id=self.user.id,
                type=OperationType.CREATE.value,
                gcal_event_id=(after_json or {}).get("id"),
                before_json=None,
                after_json=after_json,
            )
            session.add(row)
            await session.flush()
            return row.to_pyd()

    async def record_update(
        self,
        *,
        event_id: str,
        before_json: dict,
        after_json: dict,
    ) -> ChangeLogEntry:
        """Log an UPDATE (including 'move')."""
        async with get_session() as session:
            row = ChangeLogORM(
                user_id=self.user.id,
                type=OperationType.UPDATE.value,
                gcal_event_id=event_id,
                before_json=before_json,
                after_json=after_json,
            )
            session.add(row)
            await session.flush()
            return row.to_pyd()

    async def record_delete(self, *, event_id: str, before_json: dict) -> ChangeLogEntry:
        """Log a DELETE."""
        async with get_session() as session:
            row = ChangeLogORM(
                user_id=self.user.id,
                type=OperationType.DELETE.value,
                gcal_event_id=event_id,
                before_json=before_json,
                after_json=None,
            )
            session.add(row)
            await session.flush()
            return row.to_pyd()

    # ---------------------------
    # Undo operations
    # ---------------------------

    async def undo(self, *, op_id: str) -> Tuple[bool, Optional[str]]:
        """
        Undo a specific op_id.
        Returns (reverted, restored_event_id?)
        - For CREATE: deletes the created event → returns None.
        - For UPDATE: restores 'before_json' via a patch → returns same event_id.
        - For DELETE: recreates the deleted event → returns new event_id.
        """
        async with get_session() as session:
            row = await session.get(ChangeLogORM, op_id)
            if not row or row.user_id != self.user.id:
                raise NotFoundError("Operation not found")

        # Execute inverse without creating a new changelog entry
        gcal = GCalClient(self.user)

        if row.type == OperationType.CREATE.value:
            event_id = (row.after_json or {}).get("id")
            if event_id:
                await gcal.delete_event(event_id)
            return True, None

        if row.type == OperationType.UPDATE.value:
            event_id = row.gcal_event_id
            if not event_id:
                return False, None
            # Restore from before_json (full event)
            patch = _patch_from_event(row.before_json or {})
            await gcal.update_event(event_id=event_id, patch=patch)
            return True, event_id

        if row.type == OperationType.DELETE.value:
            # Recreate the deleted event from before_json
            restored_id = await _recreate_from_full(gcal, row.before_json or {})
            return True, restored_id

        # MOVE is treated as UPDATE; other types not expected in MVP
        return False, None

    async def undo_last(self) -> Tuple[bool, Optional[str]]:
        """Undo the most recent operation for this user."""
        async with get_session() as session:
            q = (
                select(ChangeLogORM)
                .where(ChangeLogORM.user_id == self.user.id)
                .order_by(desc(ChangeLogORM.timestamp))
                .limit(1)
            )
            row = (await session.execute(q)).scalar_one_or_none()
            if not row:
                return False, None
            return await self.undo(op_id=row.op_id)

    async def list_recent(self, *, limit: int = 20) -> List[ChangeLogEntry]:
        async with get_session() as session:
            q = (
                select(ChangeLogORM)
                .where(ChangeLogORM.user_id == self.user.id)
                .order_by(desc(ChangeLogORM.timestamp))
                .limit(limit)
            )
            rows = (await session.execute(q)).scalars().all()
            return [r.to_pyd() for r in rows]


# ---------------------------
# Helpers
# ---------------------------

def _patch_from_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a PATCH body from a full Google Event resource.
    Extracts a safe subset of fields we commonly modify.
    """
    if not ev:
        return {}
    patch: Dict[str, Any] = {}
    if "summary" in ev:
        patch["summary"] = ev["summary"]
    if "description" in ev:
        patch["description"] = ev["description"]
    if "location" in ev:
        patch["location"] = ev["location"]
    if "start" in ev:
        patch["start"] = ev["start"]
    if "end" in ev:
        patch["end"] = ev["end"]
    if "attendees" in ev:
        patch["attendees"] = ev["attendees"]
    if "extendedProperties" in ev:
        patch["extendedProperties"] = ev["extendedProperties"]
    return patch


async def _recreate_from_full(gcal: GCalClient, ev: Dict[str, Any]) -> Optional[str]:
    """
    Recreate a deleted event using fields from a prior full resource.
    Note: the new event will have a new event_id.
    """
    if not ev:
        return None
    title = ev.get("summary") or "Event"
    start = _extract_dt(ev.get("start"))
    end = _extract_dt(ev.get("end"))
    attendees = [a.get("email") for a in ev.get("attendees", []) if a.get("email")]
    location = ev.get("location")
    notes = ev.get("description")
    # Restore private priority if present
    priority = (ev.get("extendedProperties", {}).get("private", {}) or {}).get("priority")

    created = await gcal.create_event(
        title=title,
        start=start,
        end=end,
        attendees=attendees or None,
        location=location,
        notes=notes,
        priority=priority,
    )
    return created.get("id")


def _extract_dt(when: Dict[str, Any] | None):
    """
    Accept a Google Event 'start'/'end' object and return a timezone-aware datetime.
    We rely on GCalClient.create_event to convert tz appropriately, so returning
    an ISO string or naive datetime is acceptable; here we pass through the dict
    to GCalClient.update_event/create_event via _patch_from_event where needed.
    """
    # For recreation we rely on the event dict 'start'/'end' to include dateTime/timeZone.
    # If only 'date' (all-day), you could choose a default time; MVP ignores all-day recreation nuance.
    return when
