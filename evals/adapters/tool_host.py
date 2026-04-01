from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any, Dict, List, Tuple

from evals.models import EvalCase, EvalMetrics
from evals.module_loader import ensure_package, install_module, load_source_module


@lru_cache(maxsize=1)
def _load_tool_host_module(repo_root: Path):
    ensure_package("app")
    ensure_package("app.models")
    ensure_package("app.services")

    user_module = ModuleType("app.models.user")

    @dataclass
    class User:
        timezone: str = "America/New_York"

    user_module.User = User
    install_module("app.models.user", user_module)

    prefs_module = ModuleType("app.models.prefs")

    @dataclass
    class PrefsUpdate:
        sleep_start: str | None = None
        sleep_end: str | None = None
        min_buffer_min: int | None = None
        default_event_len_min: int | None = None

    prefs_module.PrefsUpdate = PrefsUpdate
    install_module("app.models.prefs", prefs_module)

    calendar_projection_module = ModuleType("app.services.calendar_projection")

    def summarize_event(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": event.get("id"),
            "title": event.get("summary") or event.get("title"),
            "start": _to_iso(event.get("start")),
            "end": _to_iso(event.get("end")),
            "all_day": bool(event.get("all_day", False)),
            "location": event.get("location"),
            "attendees": list(event.get("attendees", [])),
            "priority": event.get("priority"),
            "status": event.get("status", "confirmed"),
        }

    def detail_event(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **summarize_event(event),
            "notes": event.get("notes"),
            "calendar_id": event.get("calendar_id", "primary"),
            "html_link": event.get("html_link", "https://example.com/event"),
        }

    calendar_projection_module.summarize_event = summarize_event
    calendar_projection_module.detail_event = detail_event
    install_module("app.services.calendar_projection", calendar_projection_module)

    freebusy_module = ModuleType("app.services.freebusy")
    freebusy_module.STATE = {
        "free": [],
        "busy": [],
        "snapshot": {"first_slots": []},
    }

    class FreeBusyService:
        def __init__(self, gcal=None, prefs=None):
            self.gcal = gcal
            self.prefs = prefs

        async def query(self, start, end):
            return (
                list(freebusy_module.STATE.get("free", [])),
                list(freebusy_module.STATE.get("busy", [])),
            )

        async def snapshot(self, hours_ahead: int = 36):
            return dict(freebusy_module.STATE.get("snapshot", {"first_slots": []}))

    freebusy_module.FreeBusyService = FreeBusyService
    install_module("app.services.freebusy", freebusy_module)

    gcal_module = ModuleType("app.services.gcal")
    gcal_module.STATE = {
        "prefs": {
            "sleep_start": "22:30",
            "sleep_end": "07:00",
            "min_buffer_min": 15,
            "default_event_len_min": 45,
        },
        "list_events": [],
        "search_events": [],
        "events": {},
        "created_event": {"id": "evt_created"},
        "updated_event": {"id": "evt_updated"},
    }

    @dataclass
    class _Prefs:
        sleep_start: str
        sleep_end: str
        min_buffer_min: int
        default_event_len_min: int

    class GCalClient:
        def __init__(self, user):
            self.user = user

        async def get_prefs(self):
            return _Prefs(**dict(gcal_module.STATE["prefs"]))

        async def update_prefs(self, upd):
            current = dict(gcal_module.STATE["prefs"])
            for field in (
                "sleep_start",
                "sleep_end",
                "min_buffer_min",
                "default_event_len_min",
            ):
                value = getattr(upd, field, None)
                if value is not None:
                    current[field] = value
            gcal_module.STATE["prefs"] = current
            return _Prefs(**current)

        async def list_events(self, start, end, calendar_id=None, max_results=20):
            return list(gcal_module.STATE.get("list_events", []))[:max_results]

        async def search_events(self, query, start, end, calendar_id=None, max_results=10):
            return list(gcal_module.STATE.get("search_events", []))[:max_results]

        async def get_event(self, event_id, calendar_id=None):
            return dict(gcal_module.STATE.get("events", {}).get(event_id)) if event_id in gcal_module.STATE.get("events", {}) else None

        async def create_event(self, **kwargs):
            event = dict(gcal_module.STATE.get("created_event", {"id": "evt_created"}))
            event.setdefault("id", "evt_created")
            event.update(
                {
                    "summary": kwargs.get("title"),
                    "start": kwargs.get("start"),
                    "end": kwargs.get("end"),
                    "attendees": kwargs.get("attendees") or [],
                    "location": kwargs.get("location"),
                    "notes": kwargs.get("notes"),
                    "priority": kwargs.get("priority"),
                    "calendar_id": kwargs.get("calendar_id", "primary"),
                }
            )
            gcal_module.STATE.setdefault("events", {})[event["id"]] = dict(event)
            return event

        async def update_event(self, event_id, patch):
            event = dict(gcal_module.STATE.get("events", {}).get(event_id, {"id": event_id}))
            updated = dict(event)
            for key, value in dict(patch).items():
                updated[key] = value
            gcal_module.STATE.setdefault("events", {})[event_id] = dict(updated)
            return updated

        async def delete_event(self, event_id):
            gcal_module.STATE.setdefault("deleted_events", []).append(event_id)

    gcal_module.GCalClient = GCalClient
    install_module("app.services.gcal", gcal_module)

    policy_store_module = ModuleType("app.services.policy_store")
    policy_store_module.STATE = {
        "items": [],
        "next_id": 1,
    }

    @dataclass
    class _Policy:
        id: int
        text: str
        json: Dict[str, Any] | None
        active: bool

    class PolicyStore:
        def __init__(self, user):
            self.user = user

        async def create(self, text, json=None, active=True):
            policy_id = int(policy_store_module.STATE.get("next_id", 1))
            policy_store_module.STATE["next_id"] = policy_id + 1
            item = _Policy(id=policy_id, text=text, json=json, active=active)
            policy_store_module.STATE.setdefault("items", []).append(item)
            return item

        async def list_all(self):
            return list(policy_store_module.STATE.get("items", []))

        async def delete(self, policy_id):
            items = list(policy_store_module.STATE.get("items", []))
            for item in items:
                if item.id == policy_id:
                    policy_store_module.STATE["items"] = [candidate for candidate in items if candidate.id != policy_id]
                    return item
            raise ValueError(f"Unknown policy_id: {policy_id}")

    policy_store_module.PolicyStore = PolicyStore
    install_module("app.services.policy_store", policy_store_module)

    reorg_module = ModuleType("app.services.reorg")
    reorg_module.STATE = {
        "plan": {
            "moved_ids": [],
            "trimmed_ids": [],
            "pushed_ids": [],
            "op_ids": [],
        }
    }

    @dataclass
    class _Plan:
        moved_ids: List[str]
        trimmed_ids: List[str]
        pushed_ids: List[str]
        op_ids: List[str]

    class ReorgService:
        def __init__(self, gcal=None, prefs=None):
            self.gcal = gcal
            self.prefs = prefs

        async def shift_day(self, now, delay_min):
            payload = dict(reorg_module.STATE.get("plan", {}))
            return _Plan(
                moved_ids=list(payload.get("moved_ids", [])),
                trimmed_ids=list(payload.get("trimmed_ids", [])),
                pushed_ids=list(payload.get("pushed_ids", [])),
                op_ids=list(payload.get("op_ids", [])),
            )

    reorg_module.ReorgService = ReorgService
    install_module("app.services.reorg", reorg_module)

    tasks_module = ModuleType("app.services.tasks_service")
    tasks_module.STATE = {
        "list_tasks": [],
        "add_task": ("op_task_add", {"task_event_id": "task_1", "title": "Task 1"}),
        "complete_task": ("op_task_complete", {"task_event_id": "task_1", "title": "Task 1", "status": "done"}),
        "update_task": ("op_task_update", {"task_event_id": "task_1", "title": "Task 1"}),
        "delete_task": ("op_task_delete", "task_1"),
        "schedule_task": ("op_task_schedule", "evt_task_block"),
    }

    def serialize_task_item(task):
        return dict(task)

    class TasksService:
        def __init__(self, user):
            self.user = user

        async def add_task(self, title, due=None, estimate_min=None):
            op_id, task = tasks_module.STATE["add_task"]
            return op_id, dict(task)

        async def list_tasks(self, from_date=None, to_date=None):
            return [dict(item) for item in tasks_module.STATE.get("list_tasks", [])]

        async def complete_task(self, task_event_id):
            op_id, task = tasks_module.STATE["complete_task"]
            return op_id, dict(task)

        async def update_task(self, task_event_id, title=None, due=None, estimate_min=None, status=None):
            op_id, task = tasks_module.STATE["update_task"]
            updated = dict(task)
            if title is not None:
                updated["title"] = title
            if status is not None:
                updated["status"] = status
            return op_id, updated

        async def delete_task(self, task_event_id):
            return tasks_module.STATE["delete_task"]

        async def schedule_task(
            self,
            task_event_id,
            start,
            end=None,
            duration_min=None,
            title=None,
            calendar_id=None,
            priority=None,
        ):
            return tasks_module.STATE["schedule_task"]

    tasks_module.TasksService = TasksService
    tasks_module.serialize_task_item = serialize_task_item
    install_module("app.services.tasks_service", tasks_module)

    undo_module = ModuleType("app.services.undo")
    undo_module.STATE = {
        "next_op_id": 1,
        "recent": [],
        "undo_response": (False, None),
    }

    @dataclass
    class _LogItem:
        op_id: str
        type: Any
        gcal_event_id: str | None
        timestamp: Any

    @dataclass
    class _TypeValue:
        value: str

    @dataclass
    class _Entry:
        op_id: str

    class ChangeLogger:
        def __init__(self, user):
            self.user = user

        async def record_create(self, after_json):
            return _Entry(op_id=_next_op_id(undo_module))

        async def record_update(self, event_id, before_json, after_json):
            return _Entry(op_id=_next_op_id(undo_module))

        async def record_delete(self, event_id, before_json):
            return _Entry(op_id=_next_op_id(undo_module))

        async def undo(self, op_id):
            return tuple(undo_module.STATE.get("undo_response", (False, None)))

        async def undo_last(self):
            return tuple(undo_module.STATE.get("undo_response", (False, None)))

        async def list_recent(self, limit=20):
            items = []
            for item in undo_module.STATE.get("recent", [])[:limit]:
                items.append(
                    _LogItem(
                        op_id=str(item["op_id"]),
                        type=_TypeValue(value=str(item["type"])),
                        gcal_event_id=item.get("event_id"),
                        timestamp=item.get("timestamp"),
                    )
                )
            return items

    undo_module.ChangeLogger = ChangeLogger
    install_module("app.services.undo", undo_module)

    utils_module = ModuleType("app.utils")

    def from_rfc3339(value):
        if isinstance(value, str):
            return _from_iso(value)
        return value

    def to_rfc3339(value):
        return _to_iso(value)

    def utcnow():
        return _from_iso("2026-04-01T12:00:00+00:00")

    utils_module.from_rfc3339 = from_rfc3339
    utils_module.to_rfc3339 = to_rfc3339
    utils_module.utcnow = utcnow
    install_module("app.utils", utils_module)

    module = load_source_module(
        "planforge_eval_tool_host",
        repo_root / "server" / "app" / "services" / "tool_host.py",
    )
    return {
        "tool_host_module": module,
        "user_cls": user_module.User,
        "freebusy_module": freebusy_module,
        "gcal_module": gcal_module,
        "policy_store_module": policy_store_module,
        "reorg_module": reorg_module,
        "tasks_module": tasks_module,
        "undo_module": undo_module,
    }


def run_case(case: EvalCase, *, repo_root: Path) -> Tuple[Dict[str, Any], EvalMetrics]:
    loaded = _load_tool_host_module(repo_root)
    tool_host_module = loaded["tool_host_module"]
    User = loaded["user_cls"]
    freebusy_module = loaded["freebusy_module"]
    gcal_module = loaded["gcal_module"]
    policy_store_module = loaded["policy_store_module"]
    reorg_module = loaded["reorg_module"]
    tasks_module = loaded["tasks_module"]
    undo_module = loaded["undo_module"]

    _configure_tool_host_state(
        case.input,
        freebusy_module=freebusy_module,
        gcal_module=gcal_module,
        policy_store_module=policy_store_module,
        reorg_module=reorg_module,
        tasks_module=tasks_module,
        undo_module=undo_module,
    )

    user = User(timezone=str(case.input.get("user_timezone", "America/New_York")))
    dry_run = bool(case.input.get("dry_run", False))
    tool_name = str(case.input["tool_name"])
    host = tool_host_module.ToolHost(user=user, dry_run=dry_run)
    is_available = host.is_tool_available(tool_name)

    started = perf_counter()
    try:
        result = asyncio.run(host.execute(tool_name, dict(case.input.get("args", {}))))
        status = "ok"
        error = None
    except Exception as exc:
        result = None
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))

    actual = {
        "status": status,
        "tool_name": tool_name,
        "is_available": is_available,
        "error": error,
        "events_count": len(result.get("events", [])) if isinstance(result, dict) and isinstance(result.get("events"), list) else 0,
        "tasks_count": len(result.get("tasks", [])) if isinstance(result, dict) and isinstance(result.get("tasks"), list) else 0,
        "items_count": len(result.get("items", [])) if isinstance(result, dict) and isinstance(result.get("items"), list) else 0,
        "free_windows_count": len(result.get("free_windows", [])) if isinstance(result, dict) and isinstance(result.get("free_windows"), list) else 0,
        "busy_windows_count": len(result.get("busy_windows", [])) if isinstance(result, dict) and isinstance(result.get("busy_windows"), list) else 0,
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "op_id_present": bool(isinstance(result, dict) and isinstance(result.get("op_id"), str)),
        "event_id": result.get("event_id") if isinstance(result, dict) else None,
        "policy_id": result.get("id") if isinstance(result, dict) else None,
        "scheduled_event_id": result.get("scheduled_event_id") if isinstance(result, dict) else None,
        "task_event_id": result.get("task_event_id") if isinstance(result, dict) else None,
        "restored_event_id": result.get("restored_event_id") if isinstance(result, dict) else None,
        "result": result,
    }
    metrics = EvalMetrics(
        latency_ms=elapsed_ms,
        tokens_in=0,
        tokens_out=0,
        estimated_cost_usd=0.0,
        model="tool_host",
        tool_calls=1,
    )
    return actual, metrics


def _configure_tool_host_state(
    payload: Dict[str, Any],
    *,
    freebusy_module: ModuleType,
    gcal_module: ModuleType,
    policy_store_module: ModuleType,
    reorg_module: ModuleType,
    tasks_module: ModuleType,
    undo_module: ModuleType,
) -> None:
    freebusy_module.STATE = {
        "free": list(payload.get("freebusy_free", [])),
        "busy": list(payload.get("freebusy_busy", [])),
        "snapshot": dict(payload.get("freebusy_snapshot", {"first_slots": []})),
    }
    gcal_module.STATE = {
        "prefs": dict(
            payload.get(
                "prefs",
                {
                    "sleep_start": "22:30",
                    "sleep_end": "07:00",
                    "min_buffer_min": 15,
                    "default_event_len_min": 45,
                },
            )
        ),
        "list_events": list(payload.get("list_events", [])),
        "search_events": list(payload.get("search_events", [])),
        "events": dict(payload.get("events", {})),
        "created_event": dict(payload.get("created_event", {"id": "evt_created"})),
        "updated_event": dict(payload.get("updated_event", {"id": "evt_updated"})),
        "deleted_events": [],
    }
    policy_store_module.STATE = {
        "items": [],
        "next_id": int(payload.get("next_policy_id", 1)),
    }
    reorg_module.STATE = {
        "plan": dict(
            payload.get(
                "reorg_plan",
                {
                    "moved_ids": [],
                    "trimmed_ids": [],
                    "pushed_ids": [],
                    "op_ids": [],
                },
            )
        )
    }
    tasks_module.STATE = {
        "list_tasks": list(payload.get("list_tasks", [])),
        "add_task": tuple(payload.get("add_task", ("op_task_add", {"task_event_id": "task_1", "title": "Task 1"}))),
        "complete_task": tuple(
            payload.get(
                "complete_task",
                ("op_task_complete", {"task_event_id": "task_1", "title": "Task 1", "status": "done"}),
            )
        ),
        "update_task": tuple(payload.get("update_task", ("op_task_update", {"task_event_id": "task_1", "title": "Task 1"}))),
        "delete_task": tuple(payload.get("delete_task", ("op_task_delete", "task_1"))),
        "schedule_task": tuple(payload.get("schedule_task", ("op_task_schedule", "evt_task_block"))),
    }
    undo_module.STATE = {
        "next_op_id": int(payload.get("next_op_id", 1)),
        "recent": list(payload.get("recent_ops", [])),
        "undo_response": tuple(payload.get("undo_response", (False, None))),
    }


def _next_op_id(undo_module: ModuleType) -> str:
    current = int(undo_module.STATE.get("next_op_id", 1))
    undo_module.STATE["next_op_id"] = current + 1
    return f"op_{current}"


def _from_iso(value: str):
    normalized = value.replace("Z", "+00:00")
    return __import__("datetime").datetime.fromisoformat(normalized)


def _to_iso(value: Any):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
