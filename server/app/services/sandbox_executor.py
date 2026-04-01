from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.sandbox_runtime_client import SandboxRuntimeClient
from app.services.tool_host import ToolHost


_REF_PATTERN = re.compile(r"\$\{([^}]+)\}")


@dataclass
class SandboxTraceEntry:
    step_id: str
    kind: str
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None
    result: Any = None
    error: Optional[str] = None
    skipped: bool = False


@dataclass
class SandboxExecutionResult:
    status: str
    trace: List[SandboxTraceEntry]
    op_ids: List[str]
    result: Any = None
    error: Optional[str] = None


class SandboxExecutor:
    """
    Executes a small, sandbox-friendly workflow plan against the host tool
    boundary. This is the seam a future QuickJS/WASM runtime can target.
    """

    def __init__(
        self,
        tool_host: ToolHost,
        *,
        max_steps: int = 8,
        backend: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ):
        self.tool_host = tool_host
        self.max_steps = max_steps
        self.backend = (backend or os.getenv("SANDBOX_BACKEND", "python_plan")).lower()
        self.timeout_ms = timeout_ms or int(os.getenv("SANDBOX_TIMEOUT_MS", "4000"))
        self.runtime_client = (
            SandboxRuntimeClient(
                tool_host,
                timeout_ms=self.timeout_ms,
            )
            if self.backend == "quickjs_plan"
            else None
        )

    async def execute_tool(
        self,
        *,
        step_id: str,
        tool_name: str,
        args: Dict[str, Any],
    ) -> SandboxExecutionResult:
        trace: List[SandboxTraceEntry] = []
        try:
            result = await self.tool_host.execute(tool_name, args)
        except Exception as exc:
            trace.append(
                SandboxTraceEntry(
                    step_id=step_id,
                    kind="tool",
                    tool=tool_name,
                    args=args,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return SandboxExecutionResult(
                status="error",
                trace=trace,
                op_ids=[],
                error=f"{type(exc).__name__}: {exc}",
            )

        trace.append(
            SandboxTraceEntry(
                step_id=step_id,
                kind="tool",
                tool=tool_name,
                args=args,
                result=result,
            )
        )
        op_ids: List[str] = []
        _collect_op_ids_from(result, op_ids)
        return SandboxExecutionResult(
            status="ok",
            trace=trace,
            op_ids=op_ids,
            result=result,
        )

    async def run_plan(
        self,
        plan: Dict[str, Any],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> SandboxExecutionResult:
        if self.backend == "quickjs_plan":
            return await self._run_quickjs_plan(plan, context=context)
        return await self._run_python_plan(plan, context=context)

    async def _run_quickjs_plan(
        self,
        plan: Dict[str, Any],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> SandboxExecutionResult:
        if self.runtime_client is None:
            return SandboxExecutionResult(
                status="error",
                trace=[],
                op_ids=[],
                error="QuickJS sandbox runtime client is not configured",
            )

        payload = await self.runtime_client.run_plan(
            plan=plan,
            context=context or {},
            limits={
                "timeout_ms": self.timeout_ms,
                "max_steps": self.max_steps,
                "max_tool_calls": self.max_steps,
            },
        )
        return _result_from_payload(payload)

    async def _run_python_plan(
        self,
        plan: Dict[str, Any],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> SandboxExecutionResult:
        steps = plan.get("steps")
        if not isinstance(steps, list):
            return SandboxExecutionResult(
                status="error",
                trace=[],
                op_ids=[],
                error="Plan must contain a steps array",
            )
        if len(steps) > self.max_steps:
            return SandboxExecutionResult(
                status="error",
                trace=[],
                op_ids=[],
                error=f"Plan exceeds max_steps={self.max_steps}",
            )

        state: Dict[str, Any] = {"context": context or {}}
        trace: List[SandboxTraceEntry] = []
        op_ids: List[str] = []
        last_result: Any = None

        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                return SandboxExecutionResult(
                    status="error",
                    trace=trace,
                    op_ids=op_ids,
                    error=f"Step {index + 1} must be an object",
                )

            step_id = str(raw_step.get("id") or f"step_{index + 1}")
            condition = raw_step.get("if")
            if condition is not None and not self._eval_condition(condition, state):
                trace.append(
                    SandboxTraceEntry(
                        step_id=step_id,
                        kind="condition",
                        skipped=True,
                    )
                )
                continue

            if "return" in raw_step:
                returned = self._resolve_value(raw_step.get("return"), state)
                state[step_id] = returned
                trace.append(
                    SandboxTraceEntry(
                        step_id=step_id,
                        kind="return",
                        result=returned,
                    )
                )
                return SandboxExecutionResult(
                    status="ok",
                    trace=trace,
                    op_ids=op_ids,
                    result=returned,
                )

            tool_name = raw_step.get("tool")
            if not isinstance(tool_name, str) or not tool_name.strip():
                return SandboxExecutionResult(
                    status="error",
                    trace=trace,
                    op_ids=op_ids,
                    error=f"Step {step_id} is missing a tool",
                )

            args = self._resolve_value(raw_step.get("args", {}), state)
            if not isinstance(args, dict):
                return SandboxExecutionResult(
                    status="error",
                    trace=trace,
                    op_ids=op_ids,
                    error=f"Step {step_id} args must resolve to an object",
                )

            try:
                result = await self.tool_host.execute(tool_name, args)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                trace.append(
                    SandboxTraceEntry(
                        step_id=step_id,
                        kind="tool",
                        tool=tool_name,
                        args=args,
                        error=error,
                    )
                )
                return SandboxExecutionResult(
                    status="error",
                    trace=trace,
                    op_ids=op_ids,
                    error=error,
                )

            state[step_id] = result
            last_result = result
            _collect_op_ids_from(result, op_ids)
            trace.append(
                SandboxTraceEntry(
                    step_id=step_id,
                    kind="tool",
                    tool=tool_name,
                    args=args,
                    result=result,
                )
            )

        final_result = (
            self._resolve_value(plan["return"], state)
            if "return" in plan
            else last_result
        )
        return SandboxExecutionResult(
            status="ok",
            trace=trace,
            op_ids=op_ids,
            result=final_result,
        )

    def _resolve_value(self, value: Any, state: Dict[str, Any]) -> Any:
        if isinstance(value, list):
            return [self._resolve_value(item, state) for item in value]

        if isinstance(value, dict):
            if set(value.keys()) == {"$ref"} and isinstance(value["$ref"], str):
                return self._lookup_ref(value["$ref"], state)
            return {
                key: self._resolve_value(item, state)
                for key, item in value.items()
            }

        if isinstance(value, str):
            if value.startswith("$") and "${" not in value:
                return self._lookup_ref(value, state)

            def _replace(match: re.Match[str]) -> str:
                resolved = self._lookup_ref(match.group(1), state)
                if isinstance(resolved, (dict, list)):
                    raise ValueError("Cannot interpolate structured values into a string")
                return "" if resolved is None else str(resolved)

            if "${" in value:
                return _REF_PATTERN.sub(_replace, value)

        return value

    def _lookup_ref(self, raw_ref: str, state: Dict[str, Any]) -> Any:
        ref = raw_ref[1:] if raw_ref.startswith("$") else raw_ref
        if not ref:
            raise ValueError("Empty reference")

        current: Any = state
        for part in ref.split("."):
            if part == "length":
                current = len(current)
                continue

            if isinstance(current, dict):
                if part not in current:
                    raise ValueError(f"Unknown reference segment: {part}")
                current = current[part]
                continue

            if isinstance(current, list):
                try:
                    current = current[int(part)]
                except (TypeError, ValueError, IndexError) as exc:
                    raise ValueError(f"Invalid list reference segment: {part}") from exc
                continue

            raise ValueError(f"Cannot dereference {part!r} from {type(current).__name__}")

        return current

    def _eval_condition(self, expr: Any, state: Dict[str, Any]) -> bool:
        if isinstance(expr, bool):
            return expr

        if isinstance(expr, str):
            return bool(self._resolve_value(expr, state))

        if not isinstance(expr, dict):
            return bool(expr)

        if "not" in expr:
            return not self._eval_condition(expr["not"], state)

        if "exists" in expr:
            try:
                value = self._resolve_value(expr["exists"], state)
            except Exception:
                return False
            return value is not None

        if "equals" in expr:
            left, right = expr["equals"]
            return self._resolve_value(left, state) == self._resolve_value(right, state)

        if "len_equals" in expr:
            left, right = expr["len_equals"]
            return len(self._resolve_value(left, state)) == int(self._resolve_value(right, state))

        if "len_gte" in expr:
            left, right = expr["len_gte"]
            return len(self._resolve_value(left, state)) >= int(self._resolve_value(right, state))

        if "len_lte" in expr:
            left, right = expr["len_lte"]
            return len(self._resolve_value(left, state)) <= int(self._resolve_value(right, state))

        if "all" in expr:
            return all(self._eval_condition(item, state) for item in expr["all"])

        if "any" in expr:
            return any(self._eval_condition(item, state) for item in expr["any"])

        raise ValueError(f"Unsupported condition: {expr}")


def _collect_op_ids_from(result: Any, sink: List[str]) -> None:
    if not isinstance(result, dict):
        return
    if "op_id" in result and isinstance(result["op_id"], str):
        sink.append(result["op_id"])
    if "op_ids" in result and isinstance(result["op_ids"], list):
        for op_id in result["op_ids"]:
            if isinstance(op_id, str):
                sink.append(op_id)


def _result_from_payload(payload: Dict[str, Any]) -> SandboxExecutionResult:
    trace_payload = payload.get("trace")
    trace: List[SandboxTraceEntry] = []
    if isinstance(trace_payload, list):
        for item in trace_payload:
            if not isinstance(item, dict):
                continue
            trace.append(
                SandboxTraceEntry(
                    step_id=str(item.get("step_id") or ""),
                    kind=str(item.get("kind") or "tool"),
                    tool=item.get("tool"),
                    args=item.get("args") if isinstance(item.get("args"), dict) else None,
                    result=item.get("result"),
                    error=item.get("error"),
                    skipped=bool(item.get("skipped", False)),
                )
            )

    op_ids = [
        op_id
        for op_id in payload.get("op_ids", [])
        if isinstance(op_id, str)
    ] if isinstance(payload.get("op_ids"), list) else []

    return SandboxExecutionResult(
        status=str(payload.get("status") or "error"),
        trace=trace,
        op_ids=op_ids,
        result=payload.get("result"),
        error=payload.get("error"),
    )
