from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Tuple

from evals.models import EvalCase, EvalMetrics
from evals.module_loader import install_module


@lru_cache(maxsize=1)
def _load_agent_workflows(repo_root: Path):
    path = repo_root / "server" / "app" / "services" / "agent_workflows.py"
    spec = importlib.util.spec_from_file_location("planforge_agent_workflows", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load workflow module from {path}")
    module = importlib.util.module_from_spec(spec)
    install_module(spec.name, module)
    spec.loader.exec_module(module)
    return module


def run_case(case: EvalCase, *, repo_root: Path) -> Tuple[Dict[str, Any], EvalMetrics]:
    module = _load_agent_workflows(repo_root)
    service = module.AgentWorkflowService()

    started = perf_counter()
    intent = service.classify_heuristic(
        text=case.input.get("text"),
        image_url=case.input.get("image_url"),
        source=case.input.get("source"),
        dry_run=bool(case.input.get("dry_run", False)),
    )
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    workflow = service.definition_for(intent)

    actual = {
        **intent.to_dict(),
        "source": case.input.get("source", "web"),
        "dry_run": bool(case.input.get("dry_run", False)),
        "workflow_definition": workflow.to_dict(),
    }
    metrics = EvalMetrics(
        latency_ms=elapsed_ms,
        tokens_in=0,
        tokens_out=0,
        estimated_cost_usd=0.0,
        model="heuristic",
        tool_calls=0,
    )
    return actual, metrics
