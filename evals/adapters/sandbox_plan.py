from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any, Dict, Tuple

from evals.models import EvalCase, EvalMetrics
from evals.module_loader import ensure_package, install_module, load_source_module


@lru_cache(maxsize=1)
def _load_sandbox_module(repo_root: Path):
    ensure_package("app")
    ensure_package("app.services")

    runtime_module = ModuleType("app.services.sandbox_runtime_client")

    class SandboxRuntimeClient:  # pragma: no cover - only used for type/import compatibility
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def run_plan(self, *args, **kwargs):
            raise RuntimeError("QuickJS runtime client is not available in deterministic evals")

    runtime_module.SandboxRuntimeClient = SandboxRuntimeClient
    install_module("app.services.sandbox_runtime_client", runtime_module)

    tool_host_module = ModuleType("app.services.tool_host")

    class ToolHost:  # pragma: no cover - only used for type/import compatibility
        async def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            raise NotImplementedError

    tool_host_module.ToolHost = ToolHost
    install_module("app.services.tool_host", tool_host_module)

    return load_source_module(
        "planforge_eval_sandbox_executor",
        repo_root / "server" / "app" / "services" / "sandbox_executor.py",
    )


class FakeToolHost:
    def __init__(self, tool_map: Dict[str, Any]):
        self.tool_map = deepcopy(tool_map)
        self.calls = []
        self._cursor = defaultdict(int)

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"tool": tool_name, "args": deepcopy(args)})
        raw_value = self.tool_map.get(tool_name)
        value = raw_value
        if isinstance(raw_value, list):
            index = self._cursor[tool_name]
            self._cursor[tool_name] += 1
            if index >= len(raw_value):
                raise ValueError(f"No fixture response left for tool {tool_name}")
            value = raw_value[index]

        if isinstance(value, dict) and "__error__" in value:
            raise ValueError(str(value["__error__"]))

        return deepcopy(value if value is not None else {})


def run_case(case: EvalCase, *, repo_root: Path) -> Tuple[Dict[str, Any], EvalMetrics]:
    module = _load_sandbox_module(repo_root)
    tool_host = FakeToolHost(case.input.get("tool_results", {}))
    executor = module.SandboxExecutor(
        tool_host,
        max_steps=int(case.input.get("max_steps", 8)),
        backend="python_plan",
    )

    started = perf_counter()
    execution = asyncio.run(
        executor.run_plan(
            dict(case.input.get("plan", {})),
            context=dict(case.input.get("context", {})),
        )
    )
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))

    actual = {
        "status": execution.status,
        "result_status": execution.result.get("status")
        if isinstance(execution.result, dict)
        else None,
        "result_kind": execution.result.get("status")
        if isinstance(execution.result, dict)
        else execution.status,
        "op_ids": list(execution.op_ids),
        "op_ids_count": len(execution.op_ids),
        "tool_calls": len(tool_host.calls),
        "trace_tools": [step.tool for step in execution.trace if step.tool],
        "trace_kinds": [step.kind for step in execution.trace],
        "error": execution.error,
        "result": execution.result,
    }
    metrics = EvalMetrics(
        latency_ms=elapsed_ms,
        tokens_in=0,
        tokens_out=0,
        estimated_cost_usd=0.0,
        model="python_plan",
        tool_calls=len(tool_host.calls),
    )
    return actual, metrics
