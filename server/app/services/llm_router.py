# server/app/services/llm_router.py
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional

from app.llm_contract import load_system_prompt, load_tool_schemas
from app.models.policy import Policy
from app.models.prefs import Prefs
from app.models.user import User
from app.services.agent_workflows import (
    AgentWorkflowService,
    WorkflowDefinition,
    WorkflowIntent,
    WorkflowTrace,
)
from app.services.freebusy import FreeBusyService
from app.services.gcal import GCalClient
from app.services.sandbox_executor import SandboxExecutionResult, SandboxExecutor
from app.services.tool_host import ToolHost
from app.services.undo import ChangeLogger
from app.utils import to_rfc3339

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
    workflow_trace: Optional[WorkflowTrace] = None


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
        self.execution_mode = os.getenv("LLM_EXECUTION_MODE", "native_tools").lower()
        self.model = os.getenv("LLM_MODEL", "gpt-5")
        self.intent_model = os.getenv("LLM_INTENT_MODEL", self.model)
        self.enable_intent_classification = (
            os.getenv("LLM_ENABLE_INTENT_CLASSIFICATION", "true").lower()
            not in {"0", "false", "no"}
        )
        self.max_steps = int(os.getenv("LLM_MAX_STEPS", "8"))
        self.max_sandbox_steps = int(os.getenv("SANDBOX_MAX_STEPS", "8"))
        self.workflow_service = AgentWorkflowService()

    async def process_message(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        prefs: Prefs,
        policies: List[Policy],
        freebusy_snapshot: Dict[str, Any],
        source: Optional[str] = None,
        dry_run: bool = False,
    ) -> LLMResult:
        source_name = source or "web"
        user_content = self._build_user_content(text=text, image_url=image_url)
        heuristic_intent = self.workflow_service.classify_heuristic(
            text=text,
            image_url=image_url,
            source=source_name,
            dry_run=dry_run,
        )

        if self.mode == "openai" and _HAS_OPENAI and os.getenv("OPENAI_API_KEY"):
            return await self._run_openai(
                text=text,
                image_url=image_url,
                prefs=prefs,
                policies=policies,
                snapshot=freebusy_snapshot,
                source=source_name,
                dry_run=dry_run,
                user_content=user_content,
                fallback_intent=heuristic_intent,
            )

        return await self._run_stub(
            text=text,
            image_url=image_url,
            prefs=prefs,
            source=source_name,
            dry_run=dry_run,
            intent=heuristic_intent,
        )

    async def _run_openai(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        prefs: Prefs,
        policies: List[Policy],
        snapshot: Dict[str, Any],
        source: str,
        dry_run: bool,
        user_content: Any,
        fallback_intent: WorkflowIntent,
    ) -> LLMResult:
        client = AsyncOpenAI()  # requires OPENAI_API_KEY
        system_prompt = load_system_prompt()
        tool_manifest = load_tool_schemas()
        intent = await self.workflow_service.classify(
            text=text,
            image_url=image_url,
            source=source,
            dry_run=dry_run,
            user_content=user_content,
            client=client if self.enable_intent_classification else None,
            model=self.intent_model if self.enable_intent_classification else None,
            fallback=fallback_intent,
        )
        workflow = self.workflow_service.definition_for(intent)
        workflow_trace = self.workflow_service.new_trace(
            intent=intent,
            source=source,
            mode=self.mode,
            execution_mode=self.execution_mode,
        )
        tool_host = ToolHost(self.user, dry_run=dry_run)
        sandbox = SandboxExecutor(tool_host, max_steps=self.max_sandbox_steps)
        context = self._build_context(
            prefs=prefs,
            policies=policies,
            snapshot=snapshot,
            source=source,
            dry_run=dry_run,
            intent=intent,
            workflow=workflow,
        )
        started_at = perf_counter()

        if self.execution_mode == "sandbox_plan":
            return await self._run_openai_sandbox_plan(
                client=client,
                system_prompt=system_prompt,
                tool_manifest=tool_manifest,
                tool_host=tool_host,
                sandbox=sandbox,
                context=context,
                user_content=user_content,
                intent=intent,
                workflow=workflow,
                workflow_trace=workflow_trace,
                started_at=started_at,
            )

        return await self._run_openai_native_tools(
            client=client,
            system_prompt=system_prompt,
            tool_manifest=tool_manifest,
            tool_host=tool_host,
            sandbox=sandbox,
            context=context,
            user_content=user_content,
            intent=intent,
            workflow=workflow,
            workflow_trace=workflow_trace,
            started_at=started_at,
        )

    async def _run_openai_native_tools(
        self,
        *,
        client: Any,
        system_prompt: str,
        tool_manifest: Dict[str, Any],
        tool_host: ToolHost,
        sandbox: SandboxExecutor,
        context: Dict[str, Any],
        user_content: Any,
        intent: WorkflowIntent,
        workflow: WorkflowDefinition,
        workflow_trace: WorkflowTrace,
        started_at: float,
    ) -> LLMResult:
        tools_oa, name_map = self._build_tool_registry(
            tool_manifest,
            tool_host=tool_host,
            workflow=workflow,
        )
        workflow_message = self.workflow_service.workflow_system_message(
            intent,
            workflow,
            dry_run=bool(context.get("dry_run")),
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": workflow_message},
            {"role": "system", "content": "Context (JSON): " + json.dumps(context)},
            {"role": "user", "content": user_content},
        ]

        op_ids: List[str] = []

        for _ in range(self.max_steps):
            resp = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools_oa,
                tool_choice="auto",
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                for tc in tool_calls:
                    fn_name_sanitized = tc.function.name
                    fn_name = name_map.get(fn_name_sanitized, fn_name_sanitized)
                    workflow_trace.record_tool(fn_name)
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = {}

                    tool_run = await sandbox.execute_tool(
                        step_id=tc.id,
                        tool_name=fn_name,
                        args=args,
                    )
                    op_ids.extend(tool_run.op_ids)
                    result = (
                        tool_run.result
                        if tool_run.status == "ok"
                        else {"error": tool_run.error or "Execution failed"}
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=_json_default),
                        }
                    )
                continue

            content = msg.content or ""
            if not content.strip():
                content = _summary_from_op_ids(op_ids) or "Done."
            needs_clarification = _looks_like_clarification(content, op_ids=op_ids)
            status = "needs_clarification" if needs_clarification else "completed"
            result_kind = "needs_clarification" if needs_clarification else "assistant_response"
            return self._finalize_result(
                summary=content,
                op_ids=op_ids,
                workflow_trace=workflow_trace,
                started_at=started_at,
                status=status,
                needs_clarification=needs_clarification,
                result_kind=result_kind,
            )

        return self._finalize_result(
            summary=_summary_from_op_ids(op_ids) or "Action complete.",
            op_ids=op_ids,
            workflow_trace=workflow_trace,
            started_at=started_at,
            status="max_steps",
            result_kind="assistant_response",
        )

    async def _run_openai_sandbox_plan(
        self,
        *,
        client: Any,
        system_prompt: str,
        tool_manifest: Dict[str, Any],
        tool_host: ToolHost,
        sandbox: SandboxExecutor,
        context: Dict[str, Any],
        user_content: Any,
        intent: WorkflowIntent,
        workflow: WorkflowDefinition,
        workflow_trace: WorkflowTrace,
        started_at: float,
    ) -> LLMResult:
        available_tools = self._available_tools(
            tool_manifest,
            tool_host=tool_host,
            workflow=workflow,
        )
        workflow_message = self.workflow_service.workflow_system_message(
            intent,
            workflow,
            dry_run=bool(context.get("dry_run")),
        )
        planning_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": workflow_message},
            {
                "role": "system",
                "content": (
                    "Generate a JSON sandbox execution plan. Output JSON only.\n"
                    "Schema:\n"
                    "{\n"
                    '  "steps": [\n'
                    '    {"id": "search", "tool": "calendar.search", "args": {"query": "..." }},\n'
                    '    {"id": "clarify", "if": {"not": {"len_equals": ["$search.events", 1]}}, "return": {"status": "needs_clarification", "matches": "$search.events"}},\n'
                    '    {"id": "move", "tool": "calendar.move", "args": {"event_id": "$search.events.0.id", "new_start": "...", "new_end": "..."}},\n'
                    '    {"id": "done", "return": {"status": "done", "tool_result": "$move"}}\n'
                    "  ]\n"
                    "}\n"
                    "Rules:\n"
                    "- Use only the available tools.\n"
                    "- Stay inside the selected workflow unless the user message truly spans multiple domains.\n"
                    "- Prefer lookup tools before write tools.\n"
                    "- If a search can be ambiguous, add a guard return step instead of guessing.\n"
                    "- Use references like $step_id.field or $step_id.items.0.id.\n"
                    "- Supported conditions: not, exists, equals, len_equals, len_gte, len_lte, all, any.\n"
                    "- End with a return object that explains the outcome shape."
                ),
            },
            {"role": "system", "content": "Context (JSON): " + json.dumps(context)},
            {"role": "system", "content": "Available tools (JSON): " + json.dumps(available_tools)},
            {"role": "user", "content": user_content},
        ]

        plan_resp = await client.chat.completions.create(
            model=self.model,
            messages=planning_messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        plan_text = plan_resp.choices[0].message.content or "{}"
        try:
            plan = _parse_json_object(plan_text)
        except Exception as exc:
            return self._finalize_result(
                summary=f"I couldn't build a valid execution plan: {type(exc).__name__}: {exc}",
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="planning_error",
                result_kind="invalid_plan",
            )

        execution = await sandbox.run_plan(plan, context=context)
        for step in execution.trace:
            if step.tool:
                workflow_trace.record_tool(step.tool)

        summary = await self._summarize_sandbox_execution(
            client=client,
            system_prompt=system_prompt,
            workflow_message=workflow_message,
            context=context,
            user_content=user_content,
            available_tools=available_tools,
            plan=plan,
            execution=execution,
        )
        needs_clarification = _execution_needs_clarification(execution)
        status = (
            "execution_error"
            if execution.status != "ok"
            else "needs_clarification"
            if needs_clarification
            else "completed"
        )
        return self._finalize_result(
            summary=summary,
            op_ids=execution.op_ids,
            workflow_trace=workflow_trace,
            started_at=started_at,
            status=status,
            needs_clarification=needs_clarification,
            result_kind=_execution_result_kind(execution),
        )

    def _build_tool_registry(
        self,
        manifest: Dict[str, Any],
        *,
        tool_host: ToolHost,
        workflow: WorkflowDefinition,
    ) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
        tools = manifest.get("tools", [])
        oa_tools: List[Dict[str, Any]] = []
        name_map: Dict[str, str] = {}

        for tool in tools:
            original = tool["name"]
            if not tool_host.is_tool_available(original):
                continue
            if not workflow.allows_tool(original):
                continue

            sanitized = _sanitize_name(original)
            name_map[sanitized] = original
            oa_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": sanitized,
                        "description": f"{tool.get('description', original)}",
                        "parameters": tool.get("input_schema", {"type": "object"}),
                    },
                }
            )

        return oa_tools, name_map

    def _available_tools(
        self,
        manifest: Dict[str, Any],
        *,
        tool_host: ToolHost,
        workflow: WorkflowDefinition,
    ) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for tool in manifest.get("tools", []):
            if not tool_host.is_tool_available(tool["name"]):
                continue
            if not workflow.allows_tool(tool["name"]):
                continue
            tools.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", tool["name"]),
                    "input_schema": tool.get("input_schema", {"type": "object"}),
                }
            )
        return tools

    def _build_context(
        self,
        *,
        prefs: Prefs,
        policies: List[Policy],
        snapshot: Dict[str, Any],
        source: str,
        dry_run: bool,
        intent: WorkflowIntent,
        workflow: WorkflowDefinition,
    ) -> Dict[str, Any]:
        return {
            "source": source,
            "user_tz": self.user.timezone or "UTC",
            "prefs": {
                "sleep_start": prefs.sleep_start,
                "sleep_end": prefs.sleep_end,
                "min_buffer_min": prefs.min_buffer_min,
                "default_event_len_min": prefs.default_event_len_min,
            },
            "policies": [
                {"id": policy.id, "text": policy.text, "active": policy.active, "json": policy.json}
                for policy in policies
            ],
            "freebusy_hint": {
                "first_slots": snapshot.get("first_slots", []),
            },
            "dry_run": dry_run,
            "workflow": {
                **intent.to_dict(),
                **workflow.to_dict(),
            },
        }

    def _build_user_content(self, *, text: Optional[str], image_url: Optional[str]) -> Any:
        if image_url:
            return [
                {"type": "text", "text": (text or "").strip() or "See attached image."},
                {"type": "input_image", "image_url": image_url},
            ]
        return (text or "").strip() or " "

    async def _summarize_sandbox_execution(
        self,
        *,
        client: Any,
        system_prompt: str,
        workflow_message: str,
        context: Dict[str, Any],
        user_content: Any,
        available_tools: List[Dict[str, Any]],
        plan: Dict[str, Any],
        execution: SandboxExecutionResult,
    ) -> str:
        if execution.status != "ok":
            return f"I ran into an execution issue: {execution.error or 'unknown error'}."

        summary_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": workflow_message},
            {
                "role": "system",
                "content": (
                    "You are writing the final user-facing response after a sandbox execution.\n"
                    "- Only describe changes that actually happened.\n"
                    "- If the result indicates clarification is needed, ask one concise question.\n"
                    "- If no write occurred, say that plainly.\n"
                    "- Keep the response short."
                ),
            },
            {"role": "system", "content": "Context (JSON): " + json.dumps(context)},
            {"role": "system", "content": "Available tools (JSON): " + json.dumps(available_tools)},
            {"role": "system", "content": "Executed plan (JSON): " + json.dumps(plan, default=_json_default)},
            {
                "role": "system",
                "content": "Execution result (JSON): "
                + json.dumps(_execution_to_json(execution), default=_json_default),
            },
            {"role": "user", "content": user_content},
        ]
        resp = await client.chat.completions.create(
            model=self.model,
            messages=summary_messages,
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        if content.strip():
            return content
        if (
            execution.result
            and isinstance(execution.result, dict)
            and isinstance(execution.result.get("message"), str)
        ):
            return execution.result["message"]
        return _summary_from_op_ids(execution.op_ids) or "Done."

    async def _run_stub(
        self,
        *,
        text: Optional[str],
        image_url: Optional[str],
        prefs: Prefs,
        source: str,
        dry_run: bool,
        intent: WorkflowIntent,
    ) -> LLMResult:
        workflow = self.workflow_service.definition_for(intent)
        workflow_trace = self.workflow_service.new_trace(
            intent=intent,
            source=source,
            mode=self.mode,
            execution_mode=self.execution_mode,
        )
        started_at = perf_counter()
        lowered = (text or "").strip().lower()

        if intent.intent == "undo_change":
            if dry_run:
                return self._finalize_result(
                    summary="Dry run: would undo your most recent change.",
                    op_ids=[],
                    workflow_trace=workflow_trace,
                    started_at=started_at,
                    status="completed",
                    result_kind="undo_preview",
                )
            logger = ChangeLogger(self.user)
            ok, restored_id = await logger.undo_last()
            if ok:
                message = "Undid your last change."
                if restored_id:
                    message += f" Restored event {restored_id}."
                return self._finalize_result(
                    summary=message,
                    op_ids=[],
                    workflow_trace=workflow_trace,
                    started_at=started_at,
                    status="completed",
                    result_kind="undo_complete",
                )
            return self._finalize_result(
                summary="Nothing to undo.",
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="completed",
                result_kind="undo_empty",
            )

        if intent.intent == "find_availability":
            gcal = GCalClient(self.user)
            fb = FreeBusyService(gcal=gcal, prefs=prefs)
            snapshot = await fb.snapshot(hours_ahead=36)
            slots = snapshot.get("first_slots", [])
            if not slots:
                return self._finalize_result(
                    summary="You're fully booked in the next 36 hours.",
                    op_ids=[],
                    workflow_trace=workflow_trace,
                    started_at=started_at,
                    status="completed",
                    result_kind="availability_lookup",
                )
            slot_summary = "; ".join(
                [f"{_fmt_ts(item['start'])}–{_fmt_ts(item['end'])}" for item in slots]
            )
            return self._finalize_result(
                summary=f"Next open times: {slot_summary}.",
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="completed",
                result_kind="availability_lookup",
            )

        if intent.intent == "reorganize_day":
            match = re.search(r"(overslept|slept in)\s+(\d+)", lowered)
            delay = int(match.group(2)) if match else 30
            prefix = "Dry run: " if dry_run else ""
            return self._finalize_result(
                summary=(
                    f"{prefix}Got it. "
                    f"{'Would reorganize' if dry_run else 'Reorganize'} routine items by {delay} minutes "
                    "(preserving meetings and sleep)."
                ),
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="completed",
                result_kind=workflow.key,
            )

        if image_url:
            prefix = "Dry run: " if dry_run else ""
            return self._finalize_result(
                summary=f"{prefix}Received your screenshot. Image-driven scheduling is ready in OpenAI mode.",
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="completed",
                result_kind=workflow.key,
            )

        if lowered:
            prefix = "Dry run: " if dry_run else ""
            return self._finalize_result(
                summary=f"{prefix}Noted: “{text}”. No changes yet (dev stub, workflow={workflow.key}).",
                op_ids=[],
                workflow_trace=workflow_trace,
                started_at=started_at,
                status="completed",
                result_kind=workflow.key,
            )

        return self._finalize_result(
            summary="How can I help with your schedule?",
            op_ids=[],
            workflow_trace=workflow_trace,
            started_at=started_at,
            status="completed",
            result_kind=workflow.key,
        )

    def _finalize_result(
        self,
        *,
        summary: str,
        op_ids: List[str],
        workflow_trace: WorkflowTrace,
        started_at: float,
        status: str,
        needs_clarification: bool = False,
        result_kind: Optional[str] = None,
    ) -> LLMResult:
        elapsed_ms = max(0, int((perf_counter() - started_at) * 1000))
        workflow_trace.finish(
            status=status,
            op_ids=op_ids,
            elapsed_ms=elapsed_ms,
            needs_clarification=needs_clarification,
            result_kind=result_kind,
        )
        return LLMResult(summary=summary, op_ids=op_ids, workflow_trace=workflow_trace)


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed


def _execution_to_json(execution: SandboxExecutionResult) -> Dict[str, Any]:
    return {
        "status": execution.status,
        "op_ids": execution.op_ids,
        "result": execution.result,
        "error": execution.error,
        "trace": [
            {
                "step_id": step.step_id,
                "kind": step.kind,
                "tool": step.tool,
                "args": step.args,
                "result": step.result,
                "error": step.error,
                "skipped": step.skipped,
            }
            for step in execution.trace
        ],
    }


def _execution_needs_clarification(execution: SandboxExecutionResult) -> bool:
    if execution.status != "ok":
        return False
    if not isinstance(execution.result, dict):
        return False
    return str(execution.result.get("status") or "").lower() in {
        "needs_clarification",
        "clarify",
    }


def _execution_result_kind(execution: SandboxExecutionResult) -> str:
    if isinstance(execution.result, dict) and isinstance(execution.result.get("status"), str):
        return execution.result["status"]
    return execution.status


def _looks_like_clarification(text: str, *, op_ids: List[str]) -> bool:
    if op_ids:
        return False
    lowered = text.strip().lower()
    if not lowered:
        return False
    return lowered.endswith("?") or any(
        phrase in lowered
        for phrase in (
            "which one",
            "could you clarify",
            "do you mean",
            "what time works",
            "which event",
        )
    )


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
