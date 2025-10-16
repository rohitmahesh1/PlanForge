# server/app/services/tasks_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from app.models.user import User
from app.services.gcal import GCalClient, GCAL_BASE
from app.services.http import http_json
from app.services.undo import ChangeLogger
from app.services.timezone import user_now
from app.utils import to_rfc3339


TASKS_SUMMARY = "Assistant Tasks"


@dataclass
class TaskItem:
    id: str            # synthetic or same as event_id
    title: str
    due: date
    status: str        # "pending" | "done"
    event_id: str


class TasksService:
    """
    Treat tasks as all-day events on a dedicated "Assistant Tasks" calendar.
    - Add: create all-day event with ⏳ prefix and extendedProperties.private.status='pending'
    - List: read all-day events in range, filter those marked as tasks
    - Complete: flip ⏳ -> ✅ and set status='done'
    """

    def __init__(self, user: User):
        self.user = user
        self.gcal = GCalClient(user=user)
        self._tasks_calendar_id: Optional[str] = None

    # -------------------------
    # Public API
    # -------------------------

    async def ensure_tasks_calendar(self) -> str:
        if self._tasks_calendar_id:
            return self._tasks_calendar_id
        headers = await self.gcal._headers()  # reuse access token handling

        # 1) Try to find it in calendarList
        url = f"{GCAL_BASE}/users/me/calendarList"
        data = await http_json("GET", url, headers=headers, params={"minAccessRole": "owner", "maxResults": "250"})
        for item in data.get("items", []):
            if item.get("summary") == TASKS_SUMMARY:
                self._tasks_calendar_id = item["id"]
                return self._tasks_calendar_id

        # 2) Create a new calendar
        tz = self.user.timezone or "UTC"
        created = await http_json(
            "POST",
            f"{GCAL_BASE}/calendars",
            headers=headers,
            json={"summary": TASKS_SUMMARY, "timeZone": tz},
        )
        self._tasks_calendar_id = created["id"]
        return self._tasks_calendar_id

    async def add_task(self, *, title: str, due: Optional[date], estimate_min: Optional[int]) -> tuple[str, TaskItem]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()

        # Default due date = today in user's TZ
        if not due:
            due = user_now(self.user.timezone or "UTC").date()

        # All-day event spans [due, due+1)
        body = {
            "summary": f"⏳ {title}",
            "start": {"date": due.isoformat()},
            "end": {"date": (due + timedelta(days=1)).isoformat()},
            "extendedProperties": {
                "private": {
                    "task": "1",
                    "status": "pending",
                    **({"estimate_min": int(estimate_min)} if estimate_min else {}),
                }
            },
        }

        created = await http_json(
            "POST",
            f"{GCAL_BASE}/calendars/{cal_id}/events",
            headers=headers,
            json=body,
        )

        # Log as CREATE
        logger = ChangeLogger(self.user)
        entry = await logger.record_create(after_json=created)

        item = _task_from_event(created)
        return entry.op_id, item

    async def list_tasks(self, *, from_date: Optional[date], to_date: Optional[date]) -> List[TaskItem]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()

        # Default window: today → +7 days
        now = user_now(self.user.timezone or "UTC").date()
        start_d = from_date or now
        end_d = to_date or (now + timedelta(days=7))

        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": f"{start_d.isoformat()}T00:00:00Z",
            "timeMax": f"{end_d.isoformat()}T23:59:59Z",
            "maxResults": "2500",
        }
        data = await http_json("GET", f"{GCAL_BASE}/calendars/{cal_id}/events", headers=headers, params=params)

        items: List[TaskItem] = []
        for ev in data.get("items", []):
            if not _is_task_event(ev):
                continue
            items.append(_task_from_event(ev))
        return items

    async def complete_task(self, *, task_event_id: str) -> tuple[str, TaskItem]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()

        # Fetch current event to log before/after
        current = await http_json("GET", f"{GCAL_BASE}/calendars/{cal_id}/events/{task_event_id}", headers=headers)

        new_summary = _summary_done(current.get("summary") or "")
        extp = (current.get("extendedProperties") or {}).get("private", {}) or {}
        extp["status"] = "done"

        patch = {
            "summary": new_summary,
            "extendedProperties": {"private": extp},
        }
        updated = await http_json(
            "PATCH",
            f"{GCAL_BASE}/calendars/{cal_id}/events/{task_event_id}",
            headers=headers,
            json=patch,
        )

        logger = ChangeLogger(self.user)
        entry = await logger.record_update(event_id=task_event_id, before_json=current, after_json=updated)

        return entry.op_id, _task_from_event(updated)


# -------------------------
# Helpers
# -------------------------

def _is_task_event(ev: Dict[str, Any]) -> bool:
    # Task marker lives in private extendedProperties OR summary emoji
    priv = (ev.get("extendedProperties") or {}).get("private", {}) or {}
    if priv.get("task") == "1":
        return True
    summary = ev.get("summary") or ""
    return summary.strip().startswith("⏳") or summary.strip().startswith("✅")


def _task_from_event(ev: Dict[str, Any]) -> TaskItem:
    summary = (ev.get("summary") or "").strip()
    title = summary
    if summary.startswith("⏳") or summary.startswith("✅"):
        title = summary[1:].strip()

    due_iso = ((ev.get("start") or {}).get("date")) or None
    # If not an all-day event, infer due date from start.dateTime
    if not due_iso:
        dt = (ev.get("start") or {}).get("dateTime")
        if dt:
            due_iso = dt.split("T", 1)[0]
    due = date.fromisoformat(due_iso) if due_iso else date.today()

    status = ((ev.get("extendedProperties") or {}).get("private", {}) or {}).get("status") or (
        "done" if summary.startswith("✅") else "pending"
    )
    return TaskItem(
        id=ev.get("id"),
        title=title,
        due=due,
        status=status,
        event_id=ev.get("id"),
    )


def _summary_done(summary: str) -> str:
    s = summary.strip()
    if s.startswith("⏳"):
        return "✅ " + s[1:].strip()
    if s.startswith("✅"):
        return s  # already done
    return "✅ " + s
