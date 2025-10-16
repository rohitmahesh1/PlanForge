# server/app/services/llm_router.py
from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass
from typing import Optional, List, Any, Dict, Callable, Awaitable
from datetime import datetime

from app.llm_contract import load_system_prompt, load_tool_schemas
from app.models.user import User
from app.models.prefs import Prefs, PrefsUpdate
from app.models.policy import Policy
from app.services.freebusy import FreeBusyService
from app.services.gcal import GCalClient
from app.services.undo import ChangeLogger
from app.services.reorg import ReorgService
from app.services.tasks_service import TasksService
from app.services.policy_store import PolicyStore
from app.utils import to_rfc3339, from_rfc3339

# Optional OpenAI dependency (only used if LLM_ROUTER_MODE=openai)
try:
    from openai import AsyncOpenAI
    _HAS_OPENAI = True
except Exception:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore
    _HAS_OPENAI = False


@dataclass
class LLMResult:
    summary: str
    op_ids: List[str]


class LLMRouter:
    """
    Drives an LLM conversation with tool-calling to schedule/update events.
    Modes:
      - openai: uses OpenAI tool-calling with functions mapped to service methods
      - stub:   simple local logic (undo, free time, overslept acknowledgement)
    """

    def __init__(self, user: User):
        self.user = user
        self.mode = os.getenv("LLM_ROUTER_MODE", "stub").lower()  # 'openai' | 'stub'
        self.model = os.getenv("LLM_MODEL", "gpt-5")  # change if needed (must support tools; for images use a vision model)

    async def process_message(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        prefs: Prefs,
        policies: List[Policy],
        freebusy_snapshot: Dict[str, Any],
        source: Optional[str] = None,
    ) -> LLMResult:
        if self.mode == "openai" and _HAS_OPENAI and os.getenv("OPENAI_API_KEY"):
            return await self._run_openai(text=text, image_url=image_url, prefs=prefs, policies=policies, snapshot=freebusy_snapshot)
        # fallback stub
        return await self._run_stub(text=text, image_url=image_url, prefs=prefs)

    # ------------------------------------------------------------------------------------
    # OPENAI TOOL-CALLING IMPLEMENTATION
    # ------------------------------------------------------------------------------------

    async def _run_openai(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        prefs: Prefs,
        policies: List[Policy],
        snapshot: Dict[str, Any],
    ) -> LLMResult:
        """
        Uses OpenAI Chat Completions with tools that map to local service calls.
        """
        client = AsyncOpenAI()  # requires OPENAI_API_KEY
        system_prompt = load_system_prompt()
        tool_manifest = load_tool_schemas()

        # Build tool registry: OpenAI function specs + dispatcher map
        tools_oa, name_map, dispatch = self._build_tool_registry(tool_manifest)

        # Seed context to reduce unnecessary tool calls (the model may still call /prefs or /policies)
        ctx = {
            "user_tz": self.user.timezone or "UTC",
            "prefs": {
                "sleep_start": prefs.sleep_start,
                "sleep_end": prefs.sleep_end,
                "min_buffer_min": prefs.min_buffer_min,
                "default_event_len_min": prefs.default_event_len_min,
            },
            "policies": [{"id": p.id, "text": p.text, "active": p.active, "json": p.json} for p in policies],
            "freebusy_hint": {
                "first_slots": snapshot.get("first_slots", []),
            },
        }

        # Construct user content (supports optional image)
        if image_url:
            user_content = [
                {"type": "text", "text": (text or "").strip() or "See attached image."},
                {"type": "input_image", "image_url": image_url},
            ]
        else:
            user_content = (text or "").strip() or " "

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": "Context (JSON): " + json.dumps(ctx)},
            {"role": "user", "content": user_content},
        ]

        op_ids: List[str] = []
        MAX_STEPS = 8

        for _ in range(MAX_STEPS):
            resp = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools_oa,
                tool_choice="auto",
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg = choice.message

            # Handle tool calls
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                # Attach the assistant message that requested tools
                messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    } for tc in tool_calls
                ]})

                # Execute each tool and append its result to the conversation
                for tc in tool_calls:
                    fn_name_sanitized = tc.function.name
                    fn_name = name_map.get(fn_name_sanitized, fn_name_sanitized)
                    args = {}
                    try:
                        if tc.function.arguments:
                            args = json.loads(tc.function.arguments)
                    except Exception:
                        args = {}

                    # Dispatch to local services
                    try:
                        result = await dispatch[fn_name](args)
                    except Exception as e:  # be robust; feed error back to the model
                        result = {"error": f"{type(e).__name__}: {e}"}

                    # Collect op_ids if present
                    _collect_op_ids_from(result, op_ids)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=_json_default),
                    })
                # Loop – ask the model again with tool outputs
                continue

            # No tool calls – end with assistant content
            content = msg.content or ""
            # If the model didn’t return a summary, fallback to a terse recap of op_ids
            if not content.strip():
                content = _summary_from_op_ids(op_ids) or "Done."
            return LLMResult(summary=content, op_ids=op_ids)

        # Safety: if we ran out of steps
        return LLMResult(summary=_summary_from_op_ids(op_ids) or "Action complete.", op_ids=op_ids)

    # Build OpenAI tool registry and dispatcher -----------------------------------------

    def _build_tool_registry(
        self, manifest: Dict[str, Any]
    ) -> tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]]:
        """
        Convert tool_schemas.json to OpenAI tool specs and bind to async handlers.
        Returns:
          (openai_tools, sanitized_name_map, dispatcher)
        """
        tools = manifest.get("tools", [])
        oa_tools: List[Dict[str, Any]] = []
        name_map: Dict[str, str] = {}  # sanitized -> original
        dispatch: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {}

        for t in tools:
            original = t["name"]  # e.g., "calendar.create"
            sanitized = _sanitize_name(original)
            name_map[sanitized] = original

            oa_tools.append({
                "type": "function",
                "function": {
                    "name": sanitized,
                    "description": f"{t.get('description', original)}",
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            })

            # Bind dispatch handler
            dispatch[original] = self._handler_for(original)

        return oa_tools, name_map, dispatch

    def _handler_for(self, tool_name: str) -> Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]:
        """
        Map tool name to an async handler (calls our services directly).
        """
        async def _calendar_freebusy(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            prefs = await gcal.get_prefs()
            fb = FreeBusyService(gcal=gcal, prefs=prefs)
            start = _parse_dt(args.get("start"))
            end = _parse_dt(args.get("end"))
            free, busy = await fb.query(start, end)
            # Serialize datetimes to ISO
            return {
                "free_windows": [{"start": to_rfc3339(x["start"]), "end": to_rfc3339(x["end"])} for x in free],
                "busy_windows": [{"start": to_rfc3339(x["start"]), "end": to_rfc3339(x["end"]), "event_id": x.get("event_id")} for x in busy],
            }

        async def _calendar_create(args: Dict[str, Any]) -> Dict[str, Any]:
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

        async def _calendar_update(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            event_id = args["event_id"]
            before = await gcal.get_event(event_id)
            updated = await gcal.update_event(event_id=event_id, patch=args["patch"])
            logger = ChangeLogger(self.user)
            entry = await logger.record_update(event_id=event_id, before_json=before or {}, after_json=updated)
            return {"op_id": entry.op_id, "event_id": event_id}

        async def _calendar_move(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            event_id = args["event_id"]
            before = await gcal.get_event(event_id)
            updated = await gcal.update_event(event_id=event_id, patch={"start": _parse_dt(args["new_start"]), "end": _parse_dt(args["new_end"])})
            logger = ChangeLogger(self.user)
            entry = await logger.record_update(event_id=event_id, before_json=before or {}, after_json=updated)
            return {"op_id": entry.op_id, "event_id": event_id}

        async def _calendar_delete(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            event_id = args["event_id"]
            before = await gcal.get_event(event_id)
            await gcal.delete_event(event_id)
            logger = ChangeLogger(self.user)
            entry = await logger.record_delete(event_id=event_id, before_json=before or {})
            return {"op_id": entry.op_id}

        async def _calendar_reorg_today(args: Dict[str, Any]) -> Dict[str, Any]:
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

        async def _tasks_add(args: Dict[str, Any]) -> Dict[str, Any]:
            svc = TasksService(self.user)
            due = args.get("due")
            # If present as string date, leave as-is; service handles defaulting.
            op_id, task = await svc.add_task(
                title=args["title"],
                due=datetime.fromisoformat(due).date() if isinstance(due, str) else None,
                estimate_min=args.get("estimate_min"),
            )
            return {"op_id": op_id, "task": {
                "id": task.id,
                "title": task.title,
                "due": task.due.isoformat(),
                "status": task.status,
                "event_id": task.event_id,
            }}

        async def _ops_undo(args: Dict[str, Any]) -> Dict[str, Any]:
            logger = ChangeLogger(self.user)
            op_id = args.get("op_id")
            if op_id:
                ok, restored = await logger.undo(op_id=op_id)
            else:
                ok, restored = await logger.undo_last()
            return {"reverted": bool(ok), "restored_event_id": restored}

        async def _prefs_get(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            p = await gcal.get_prefs()
            return {
                "sleep_start": p.sleep_start,
                "sleep_end": p.sleep_end,
                "min_buffer_min": p.min_buffer_min,
                "default_event_len_min": p.default_event_len_min,
            }

        async def _prefs_update(args: Dict[str, Any]) -> Dict[str, Any]:
            gcal = GCalClient(self.user)
            upd = PrefsUpdate(**args)
            p = await gcal.update_prefs(upd)
            return {
                "sleep_start": p.sleep_start,
                "sleep_end": p.sleep_end,
                "min_buffer_min": p.min_buffer_min,
                "default_event_len_min": p.default_event_len_min,
            }

        async def _policies_save(args: Dict[str, Any]) -> Dict[str, Any]:
            store = PolicyStore(self.user)
            p = await store.create(text=args["text"], json=args.get("json"), active=bool(args.get("active", True)))
            return {"id": p.id, "text": p.text, "json": p.json, "active": p.active}

        async def _policies_list(args: Dict[str, Any]) -> Dict[str, Any]:
            store = PolicyStore(self.user)
            items = await store.list_all()
            return {"items": [{"id": p.id, "text": p.text, "json": p.json, "active": p.active} for p in items]}

        async def _policies_delete(args: Dict[str, Any]) -> Dict[str, Any]:
            store = PolicyStore(self.user)
            p = await store.delete(args["policy_id"])
            return {"id": p.id, "text": p.text, "json": p.json, "active": p.active}

        mapping: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "calendar.freebusy": _calendar_freebusy,
            "calendar.create": _calendar_create,
            "calendar.update": _calendar_update,
            "calendar.move": _calendar_move,
            "calendar.delete": _calendar_delete,
            "calendar.reorg_today": _calendar_reorg_today,
            "tasks.add": _tasks_add,
            "ops.undo": _ops_undo,
            "prefs.get": _prefs_get,
            "prefs.update": _prefs_update,
            "policies.save": _policies_save,
            "policies.list": _policies_list,
            "policies.delete": _policies_delete,
        }
        return mapping

    # ------------------------------------------------------------------------------------
    # STUB MODE (fallback)
    # ------------------------------------------------------------------------------------

    async def _run_stub(self, *, text: Optional[str], image_url: Optional[str], prefs: Prefs) -> LLMResult:
        t = (text or "").strip().lower()

        # Undo
        if re.search(r"\bundo\b", t):
            logger = ChangeLogger(self.user)
            ok, restored_id = await logger.undo_last()
            if ok:
                msg = "Undid your last change."
                if restored_id:
                    msg += f" Restored event {restored_id}."
                return LLMResult(summary=msg, op_ids=[])
            return LLMResult(summary="Nothing to undo.", op_ids=[])

        # Quick availability peek
        if re.search(r"\bfree\b", t) or re.search(r"\bavailability\b", t):
            gcal = GCalClient(self.user)
            fb = FreeBusyService(gcal=gcal, prefs=prefs)
            snap = await fb.snapshot(hours_ahead=36)
            slots = snap.get("first_slots", [])
            if not slots:
                return LLMResult(summary="You're fully booked in the next 36 hours.", op_ids=[])
            s = "; ".join([f"{_fmt_ts(x['start'])}–{_fmt_ts(x['end'])}" for x in slots])
            return LLMResult(summary=f"Next open times: {s}.", op_ids=[])

        # Overslept acknowledgment
        m = re.search(r"(overslept|slept in)\s+(\d+)", t)
        if m:
            mins = int(m.group(2))
            return LLMResult(
                summary=f"Got it — would reorganize routine items by {mins} minutes (preserving meetings and sleep).",
                op_ids=[],
            )

        # Fallback
        if t:
            return LLMResult(summary=f"Noted: “{text}”. No changes yet (dev stub).", op_ids=[])
        if image_url:
            return LLMResult(summary="Received your screenshot. OCR + scheduling is coming next.", op_ids=[])
        return LLMResult(summary="How can I help with your schedule?", op_ids=[])


# ========================================================================================
# Helpers
# ========================================================================================

def _sanitize_name(name: str) -> str:
    """OpenAI function names must be simple identifiers."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)

def _parse_dt(val: Any) -> datetime:
    """Accept ISO 8601 (with Z) or datetime; return datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return from_rfc3339(val)
    raise ValueError(f"Invalid datetime value: {val!r}")

def _collect_op_ids_from(result: Any, sink: List[str]) -> None:
    if not isinstance(result, dict):
        return
    if "op_id" in result and isinstance(result["op_id"], str):
        sink.append(result["op_id"])
    if "op_ids" in result and isinstance(result["op_ids"], list):
        for oid in result["op_ids"]:
            if isinstance(oid, str):
                sink.append(oid)

def _summary_from_op_ids(op_ids: List[str]) -> str:
    if not op_ids:
        return ""
    return f"Completed {len(op_ids)} change(s). Undo with “undo” if needed."

def _fmt_ts(dtobj: Any) -> str:
    try:
        return dtobj.strftime("%a %I:%M%p").lstrip("0").replace(" 0", " ")
    except Exception:
        return str(dtobj)

def _json_default(o: Any):
    if isinstance(o, datetime):
        return to_rfc3339(o)
    return str(o)
