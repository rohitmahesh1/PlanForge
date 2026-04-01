from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Awaitable

from app.models.prefs import PrefsUpdate
from app.models.user import User
from app.services.calendar_projection import detail_event, summarize_event
from app.services.freebusy import FreeBusyService
from app.services.gcal import GCalClient
from app.services.policy_store import PolicyStore
from app.services.reorg import ReorgService
from app.services.tasks_service import TasksService, serialize_task_item
from app.services.undo import ChangeLogger
from app.utils import from_rfc3339, to_rfc3339, utcnow


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


READ_ONLY_TOOLS = {
    "calendar.freebusy",
    "calendar.list",
    "calendar.search",
    "calendar.get",
    "tasks.list",
    "ops.history",
    "prefs.get",
    "policies.list",
}


class ToolHost:
    """
    Host-side tool execution boundary for the planner.

    Today the host still calls local Python services directly. The important
    refactor is that the router no longer owns those side effects, which gives
    us a clean seam for a future QuickJS/WASM executor to call into.
    """

    def __init__(self, user: User, *, dry_run: bool = False):
        self.user = user
        self.dry_run = dry_run
        self._handlers: Dict[str, ToolHandler] = {
            "calendar.freebusy": self._calendar_freebusy,
            "calendar.list": self._calendar_list,
            "calendar.search": self._calendar_search,
            "calendar.get": self._calendar_get,
            "calendar.create": self._calendar_create,
            "calendar.update": self._calendar_update,
            "calendar.move": self._calendar_move,
            "calendar.delete": self._calendar_delete,
            "calendar.reorg_today": self._calendar_reorg_today,
            "tasks.add": self._tasks_add,
            "tasks.list": self._tasks_list,
            "tasks.complete": self._tasks_complete,
            "tasks.update": self._tasks_update,
            "tasks.delete": self._tasks_delete,
            "tasks.schedule": self._tasks_schedule,
            "ops.undo": self._ops_undo,
            "ops.history": self._ops_history,
            "prefs.get": self._prefs_get,
            "prefs.update": self._prefs_update,
            "policies.save": self._policies_save,
            "policies.list": self._policies_list,
            "policies.delete": self._policies_delete,
        }

    def is_tool_available(self, tool_name: str) -> bool:
        if tool_name not in self._handlers:
            return False
        if self.dry_run and tool_name not in READ_ONLY_TOOLS:
            return False
        return True

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name not in self._handlers:
            raise ValueError(f"Unknown tool: {tool_name}")
        if self.dry_run and tool_name not in READ_ONLY_TOOLS:
            raise ValueError(f"Tool {tool_name} is not available in dry-run mode")
        return await self._handlers[tool_name](args)

    async def _calendar_freebusy(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        prefs = await gcal.get_prefs()
        fb = FreeBusyService(gcal=gcal, prefs=prefs)
        start = _parse_dt(args.get("start"))
        end = _parse_dt(args.get("end"))
        free, busy = await fb.query(start, end)
        return {
            "free_windows": [
                {"start": to_rfc3339(x["start"]), "end": to_rfc3339(x["end"])}
                for x in free
            ],
            "busy_windows": [
                {
                    "start": to_rfc3339(x["start"]),
                    "end": to_rfc3339(x["end"]),
                    "event_id": x.get("event_id"),
                }
                for x in busy
            ],
        }

    async def _calendar_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        items = await gcal.list_events(
            _parse_dt(args["start"]),
            _parse_dt(args["end"]),
            calendar_id=args.get("calendar_id"),
            max_results=int(args.get("limit", 20)),
        )
        return {"events": [summarize_event(item) for item in items]}

    async def _calendar_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        now = utcnow()
        start = _parse_optional_dt(args.get("start")) or (now - timedelta(days=30))
        end = _parse_optional_dt(args.get("end")) or (now + timedelta(days=90))
        items = await gcal.search_events(
            query=str(args["query"]),
            start=start,
            end=end,
            calendar_id=args.get("calendar_id"),
            max_results=int(args.get("limit", 10)),
        )
        return {"events": [summarize_event(item) for item in items]}

    async def _calendar_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        event = await gcal.get_event(
            args["event_id"],
            calendar_id=args.get("calendar_id"),
        )
        if not event:
            raise ValueError("Event not found")
        return detail_event(event)

    async def _calendar_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        ev = await gcal.create_event(
            title=args["title"],
            start=_parse_dt(args["start"]),
            end=_parse_dt(args["end"]),
            attendees=args.get("attendees"),
            location=args.get("location"),
            notes=args.get("notes"),
            calendar_id=args.get("calendar_id"),
            priority=args.get("priority"),
        )
        logger = ChangeLogger(self.user)
        entry = await logger.record_create(after_json=ev)
        return {"op_id": entry.op_id, "event_id": ev.get("id")}

    async def _calendar_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        event_id = args["event_id"]
        before = await gcal.get_event(event_id)
        updated = await gcal.update_event(event_id=event_id, patch=args["patch"])
        logger = ChangeLogger(self.user)
        entry = await logger.record_update(
            event_id=event_id,
            before_json=before or {},
            after_json=updated,
        )
        return {"op_id": entry.op_id, "event_id": event_id}

    async def _calendar_move(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        event_id = args["event_id"]
        before = await gcal.get_event(event_id)
        updated = await gcal.update_event(
            event_id=event_id,
            patch={
                "start": _parse_dt(args["new_start"]),
                "end": _parse_dt(args["new_end"]),
            },
        )
        logger = ChangeLogger(self.user)
        entry = await logger.record_update(
            event_id=event_id,
            before_json=before or {},
            after_json=updated,
        )
        return {"op_id": entry.op_id, "event_id": event_id}

    async def _calendar_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        event_id = args["event_id"]
        before = await gcal.get_event(event_id)
        await gcal.delete_event(event_id)
        logger = ChangeLogger(self.user)
        entry = await logger.record_delete(event_id=event_id, before_json=before or {})
        return {"op_id": entry.op_id}

    async def _calendar_reorg_today(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        prefs = await gcal.get_prefs()
        svc = ReorgService(gcal=gcal, prefs=prefs)
        plan = await svc.shift_day(now=_parse_dt(args["now"]), delay_min=int(args["delay_min"]))
        return {
            "moved": plan.moved_ids,
            "trimmed": plan.trimmed_ids,
            "pushed": plan.pushed_ids,
            "op_ids": plan.op_ids,
        }

    async def _tasks_add(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        due = args.get("due")
        op_id, task = await svc.add_task(
            title=args["title"],
            due=datetime.fromisoformat(due).date() if isinstance(due, str) else None,
            estimate_min=args.get("estimate_min"),
        )
        return {"op_id": op_id, "task": serialize_task_item(task)}

    async def _tasks_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        items = await svc.list_tasks(
            from_date=_parse_date(args.get("from_date")),
            to_date=_parse_date(args.get("to_date")),
        )
        return {"tasks": [serialize_task_item(task) for task in items]}

    async def _tasks_complete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        op_id, task = await svc.complete_task(task_event_id=args["task_event_id"])
        return {"op_id": op_id, "task": serialize_task_item(task)}

    async def _tasks_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        op_id, task = await svc.update_task(
            task_event_id=args["task_event_id"],
            title=args.get("title"),
            due=_parse_date(args.get("due")),
            estimate_min=args.get("estimate_min"),
            status=args.get("status"),
        )
        return {"op_id": op_id, "task": serialize_task_item(task)}

    async def _tasks_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        op_id, task_event_id = await svc.delete_task(task_event_id=args["task_event_id"])
        return {"op_id": op_id, "task_event_id": task_event_id}

    async def _tasks_schedule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        svc = TasksService(self.user)
        op_id, scheduled_event_id = await svc.schedule_task(
            task_event_id=args["task_event_id"],
            start=_parse_dt(args["start"]),
            end=_parse_optional_dt(args.get("end")),
            duration_min=args.get("duration_min"),
            title=args.get("title"),
            calendar_id=args.get("calendar_id"),
            priority=args.get("priority"),
        )
        return {
            "op_id": op_id,
            "task_event_id": args["task_event_id"],
            "scheduled_event_id": scheduled_event_id,
        }

    async def _ops_undo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        logger = ChangeLogger(self.user)
        op_id = args.get("op_id")
        if op_id:
            ok, restored = await logger.undo(op_id=op_id)
        else:
            ok, restored = await logger.undo_last()
        return {"reverted": bool(ok), "restored_event_id": restored}

    async def _ops_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        logger = ChangeLogger(self.user)
        items = await logger.list_recent(limit=int(args.get("limit", 20)))
        return {
            "items": [
                {
                    "op_id": item.op_id,
                    "type": item.type.value,
                    "event_id": item.gcal_event_id,
                    "timestamp": to_rfc3339(item.timestamp),
                }
                for item in items
            ]
        }

    async def _prefs_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        p = await gcal.get_prefs()
        return {
            "sleep_start": p.sleep_start,
            "sleep_end": p.sleep_end,
            "min_buffer_min": p.min_buffer_min,
            "default_event_len_min": p.default_event_len_min,
        }

    async def _prefs_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        gcal = GCalClient(self.user)
        upd = PrefsUpdate(**args)
        p = await gcal.update_prefs(upd)
        return {
            "sleep_start": p.sleep_start,
            "sleep_end": p.sleep_end,
            "min_buffer_min": p.min_buffer_min,
            "default_event_len_min": p.default_event_len_min,
        }

    async def _policies_save(self, args: Dict[str, Any]) -> Dict[str, Any]:
        store = PolicyStore(self.user)
        p = await store.create(
            text=args["text"],
            json=args.get("json"),
            active=bool(args.get("active", True)),
        )
        return {"id": p.id, "text": p.text, "json": p.json, "active": p.active}

    async def _policies_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        store = PolicyStore(self.user)
        items = await store.list_all()
        return {
            "items": [
                {"id": p.id, "text": p.text, "json": p.json, "active": p.active}
                for p in items
            ]
        }

    async def _policies_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        store = PolicyStore(self.user)
        p = await store.delete(args["policy_id"])
        return {"id": p.id, "text": p.text, "json": p.json, "active": p.active}


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return from_rfc3339(val)
    raise ValueError(f"Invalid datetime value: {val!r}")


def _parse_optional_dt(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    return _parse_dt(val)


def _parse_date(val: Any) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val).date()
    raise ValueError(f"Invalid date value: {val!r}")
