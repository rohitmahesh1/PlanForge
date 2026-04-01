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
from app.services.errors import NotFoundError


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
    - Update/Delete: patch or remove the underlying all-day task event
    - Schedule: create a linked work block on the user's primary calendar
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
        current = await self._get_task_event(task_event_id)

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

    async def update_task(
        self,
        *,
        task_event_id: str,
        title: Optional[str] = None,
        due: Optional[date] = None,
        estimate_min: Optional[int] = None,
        status: Optional[str] = None,
    ) -> tuple[str, TaskItem]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()
        current = await self._get_task_event(task_event_id)
        current_task = _task_from_event(current)

        next_status = status or current_task.status
        next_title = title or current_task.title
        patch: Dict[str, Any] = {
            "summary": _summary_with_status(next_title, next_status),
        }

        if due is not None:
            patch["start"] = {"date": due.isoformat()}
            patch["end"] = {"date": (due + timedelta(days=1)).isoformat()}

        extp = (current.get("extendedProperties") or {}).get("private", {}) or {}
        extp["task"] = "1"
        extp["status"] = next_status
        if estimate_min is not None:
            extp["estimate_min"] = str(int(estimate_min))
        patch["extendedProperties"] = {"private": extp}

        updated = await http_json(
            "PATCH",
            f"{GCAL_BASE}/calendars/{cal_id}/events/{task_event_id}",
            headers=headers,
            json=patch,
        )

        logger = ChangeLogger(self.user)
        entry = await logger.record_update(
            event_id=task_event_id,
            before_json=current,
            after_json=updated,
        )
        return entry.op_id, _task_from_event(updated)

    async def delete_task(self, *, task_event_id: str) -> tuple[str, str]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()
        current = await self._get_task_event(task_event_id)

        await http_json(
            "DELETE",
            f"{GCAL_BASE}/calendars/{cal_id}/events/{task_event_id}",
            headers=headers,
        )

        logger = ChangeLogger(self.user)
        entry = await logger.record_delete(event_id=task_event_id, before_json=current)
        return entry.op_id, task_event_id

    async def schedule_task(
        self,
        *,
        task_event_id: str,
        start: datetime,
        end: Optional[datetime] = None,
        duration_min: Optional[int] = None,
        title: Optional[str] = None,
        calendar_id: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> tuple[str, str]:
        current = await self._get_task_event(task_event_id)
        task = _task_from_event(current)
        prefs = await self.gcal.get_prefs()
        extp = (current.get("extendedProperties") or {}).get("private", {}) or {}

        effective_duration = duration_min
        if effective_duration is None:
            raw_estimate = extp.get("estimate_min")
            try:
                effective_duration = int(raw_estimate) if raw_estimate is not None else None
            except (TypeError, ValueError):
                effective_duration = None
        if effective_duration is None:
            effective_duration = prefs.default_event_len_min

        scheduled_end = end or (start + timedelta(minutes=max(5, int(effective_duration))))
        event_title = title or f"Work on: {task.title}"
        notes = f"Linked task event: {task_event_id}"

        created = await self.gcal.create_event(
            title=event_title,
            start=start,
            end=scheduled_end,
            notes=notes,
            calendar_id=calendar_id,
            priority=priority or "routine",
            private_properties={"linked_task_event_id": task_event_id},
        )

        logger = ChangeLogger(self.user)
        entry = await logger.record_create(after_json=created)
        return entry.op_id, created.get("id") or ""

    async def _get_task_event(self, task_event_id: str) -> Dict[str, Any]:
        cal_id = await self.ensure_tasks_calendar()
        headers = await self.gcal._headers()
        try:
            event = await http_json(
                "GET",
                f"{GCAL_BASE}/calendars/{cal_id}/events/{task_event_id}",
                headers=headers,
            )
        except Exception as exc:
            if "404" in str(exc):
                raise NotFoundError("Task not found") from exc
            raise
        if not _is_task_event(event):
            raise NotFoundError("Task not found")
        return event


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


def _summary_with_status(title: str, status: str) -> str:
    clean = title.strip()
    if status == "done":
        return f"✅ {clean}"
    return f"⏳ {clean}"


def serialize_task_item(task: TaskItem) -> Dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "due": task.due.isoformat(),
        "status": task.status,
        "event_id": task.event_id,
    }
