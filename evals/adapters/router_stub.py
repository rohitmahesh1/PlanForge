from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any, Dict, List, Tuple

from evals.models import EvalCase, EvalMetrics
from evals.module_loader import ensure_package, install_module, load_source_module


@lru_cache(maxsize=1)
def _load_router_module(repo_root: Path):
    ensure_package("app")
    ensure_package("app.models")
    ensure_package("app.services")

    llm_contract_module = ModuleType("app.llm_contract")
    llm_contract_module.load_system_prompt = lambda: "stub prompt"
    llm_contract_module.load_tool_schemas = lambda: {"tools": []}
    install_module("app.llm_contract", llm_contract_module)

    user_module = ModuleType("app.models.user")

    @dataclass
    class User:
        timezone: str = "America/New_York"

    user_module.User = User
    install_module("app.models.user", user_module)

    prefs_module = ModuleType("app.models.prefs")

    @dataclass
    class Prefs:
        sleep_start: str = "22:30"
        sleep_end: str = "07:00"
        min_buffer_min: int = 15
        default_event_len_min: int = 45

    prefs_module.Prefs = Prefs
    install_module("app.models.prefs", prefs_module)

    policy_module = ModuleType("app.models.policy")

    @dataclass
    class Policy:
        id: int
        text: str
        active: bool = True
        json: Dict[str, Any] | None = None

    policy_module.Policy = Policy
    install_module("app.models.policy", policy_module)

    agent_workflows = load_source_module(
        "app.services.agent_workflows",
        repo_root / "server" / "app" / "services" / "agent_workflows.py",
    )

    freebusy_module = ModuleType("app.services.freebusy")
    freebusy_module.CURRENT_SNAPSHOT = {"first_slots": []}

    class FreeBusyService:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def snapshot(self, hours_ahead: int = 36):
            return dict(freebusy_module.CURRENT_SNAPSHOT)

    freebusy_module.FreeBusyService = FreeBusyService
    install_module("app.services.freebusy", freebusy_module)

    gcal_module = ModuleType("app.services.gcal")

    class GCalClient:
        def __init__(self, user):
            self.user = user

    gcal_module.GCalClient = GCalClient
    install_module("app.services.gcal", gcal_module)

    sandbox_module = ModuleType("app.services.sandbox_executor")

    @dataclass
    class SandboxExecutionResult:
        status: str
        trace: List[Any]
        op_ids: List[str]
        result: Any = None
        error: str | None = None

    class SandboxExecutor:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    sandbox_module.SandboxExecutionResult = SandboxExecutionResult
    sandbox_module.SandboxExecutor = SandboxExecutor
    install_module("app.services.sandbox_executor", sandbox_module)

    tool_host_module = ModuleType("app.services.tool_host")

    class ToolHost:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    tool_host_module.ToolHost = ToolHost
    install_module("app.services.tool_host", tool_host_module)

    undo_module = ModuleType("app.services.undo")
    undo_module.UNDO_RESPONSE = (False, None)

    class ChangeLogger:
        def __init__(self, user):
            self.user = user

        async def undo_last(self):
            return undo_module.UNDO_RESPONSE

    undo_module.ChangeLogger = ChangeLogger
    install_module("app.services.undo", undo_module)

    utils_module = ModuleType("app.utils")
    utils_module.to_rfc3339 = lambda value: str(value)
    install_module("app.utils", utils_module)

    module = load_source_module(
        "planforge_eval_llm_router",
        repo_root / "server" / "app" / "services" / "llm_router.py",
    )
    return {
        "router_module": module,
        "user_cls": user_module.User,
        "prefs_cls": prefs_module.Prefs,
        "policy_cls": policy_module.Policy,
        "freebusy_module": freebusy_module,
        "undo_module": undo_module,
        "agent_workflows_module": agent_workflows,
    }


def run_case(case: EvalCase, *, repo_root: Path) -> Tuple[Dict[str, Any], EvalMetrics]:
    loaded = _load_router_module(repo_root)
    router_module = loaded["router_module"]
    User = loaded["user_cls"]
    Prefs = loaded["prefs_cls"]
    Policy = loaded["policy_cls"]
    freebusy_module = loaded["freebusy_module"]
    undo_module = loaded["undo_module"]

    user = User(timezone=str(case.input.get("user_timezone", "America/New_York")))
    prefs_payload = dict(case.input.get("prefs", {}))
    prefs = Prefs(
        sleep_start=str(prefs_payload.get("sleep_start", "22:30")),
        sleep_end=str(prefs_payload.get("sleep_end", "07:00")),
        min_buffer_min=int(prefs_payload.get("min_buffer_min", 15)),
        default_event_len_min=int(prefs_payload.get("default_event_len_min", 45)),
    )
    policies = [
        Policy(
            id=int(item.get("id", index + 1)),
            text=str(item.get("text", "")),
            active=bool(item.get("active", True)),
            json=item.get("json"),
        )
        for index, item in enumerate(case.input.get("policies", []))
    ]

    freebusy_module.CURRENT_SNAPSHOT = dict(case.input.get("snapshot", {"first_slots": []}))
    undo_response = case.input.get("undo_response")
    if isinstance(undo_response, list):
        undo_module.UNDO_RESPONSE = tuple(undo_response)
    elif isinstance(undo_response, tuple):
        undo_module.UNDO_RESPONSE = undo_response
    else:
        undo_module.UNDO_RESPONSE = (False, None)

    previous_mode = os.environ.get("LLM_ROUTER_MODE")
    os.environ["LLM_ROUTER_MODE"] = "stub"
    try:
        router = router_module.LLMRouter(user=user)
        started = perf_counter()
        result = asyncio.run(
            router.process_message(
                text=case.input.get("text"),
                image_url=case.input.get("image_url"),
                prefs=prefs,
                policies=policies,
                freebusy_snapshot=dict(case.input.get("snapshot", {})),
                source=case.input.get("source", "web"),
                dry_run=bool(case.input.get("dry_run", False)),
            )
        )
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    finally:
        if previous_mode is None:
            os.environ.pop("LLM_ROUTER_MODE", None)
        else:
            os.environ["LLM_ROUTER_MODE"] = previous_mode

    trace = result.workflow_trace.to_dict() if result.workflow_trace else {}
    actual = {
        "summary": result.summary,
        "intent": trace.get("intent"),
        "workflow": trace.get("workflow"),
        "trace_status": trace.get("status"),
        "result_kind": trace.get("result_kind"),
        "needs_clarification": trace.get("needs_clarification"),
        "tool_calls": trace.get("tool_call_count", 0),
        "op_ids_count": len(result.op_ids or []),
        "used_tools": list(trace.get("used_tools", [])),
    }
    metrics = EvalMetrics(
        latency_ms=elapsed_ms,
        tokens_in=0,
        tokens_out=0,
        estimated_cost_usd=0.0,
        model="stub_router",
        tool_calls=int(trace.get("tool_call_count", 0) or 0),
    )
    return actual, metrics
