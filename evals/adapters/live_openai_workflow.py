from __future__ import annotations

import asyncio
import importlib.util
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Tuple

from evals.models import EvalCase, EvalMetrics
from evals.module_loader import install_module
from evals.pricing import estimate_openai_cost_usd


@lru_cache(maxsize=1)
def _load_agent_workflows(repo_root: Path):
    path = repo_root / "server" / "app" / "services" / "agent_workflows.py"
    spec = importlib.util.spec_from_file_location("planforge_live_agent_workflows", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load workflow module from {path}")
    module = importlib.util.module_from_spec(spec)
    install_module(spec.name, module)
    spec.loader.exec_module(module)
    return module


def run_case(
    case: EvalCase,
    *,
    repo_root: Path,
    allow_live: bool,
    default_model: str | None,
) -> Tuple[Dict[str, Any], EvalMetrics]:
    if not allow_live:
        raise RuntimeError(
            "Live OpenAI evals are disabled. Re-run with --include-live --allow-live."
        )

    try:
        from openai import AsyncOpenAI
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "The openai package is required for live workflow evals."
        ) from exc

    module = _load_agent_workflows(repo_root)
    service = module.AgentWorkflowService()
    base_client = AsyncOpenAI()
    capturing_client = _UsageCapturingClient(base_client)

    model = str(case.input.get("model") or default_model or "gpt-5")
    user_content = _build_user_content(
        text=case.input.get("text"),
        image_url=case.input.get("image_url"),
    )

    started = perf_counter()
    intent = asyncio.run(
        service.classify(
            text=case.input.get("text"),
            image_url=case.input.get("image_url"),
            source=case.input.get("source"),
            dry_run=bool(case.input.get("dry_run", False)),
            user_content=user_content,
            client=capturing_client,
            model=model,
            fallback=None,
        )
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
        tokens_in=capturing_client.prompt_tokens,
        tokens_out=capturing_client.completion_tokens,
        estimated_cost_usd=estimate_openai_cost_usd(
            model=model,
            tokens_in=capturing_client.prompt_tokens,
            tokens_out=capturing_client.completion_tokens,
            pricing=case.input.get("pricing"),
        ),
        model=model,
        tool_calls=0,
    )
    return actual, metrics


def _build_user_content(*, text: Any, image_url: Any) -> Any:
    raw_text = (text or "").strip() if isinstance(text, str) else ""
    if image_url:
        return [
            {"type": "text", "text": raw_text or "See attached image."},
            {"type": "input_image", "image_url": image_url},
        ]
    return raw_text or " "


class _UsageCapturingClient:
    def __init__(self, inner: Any):
        self.inner = inner
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.chat = _UsageCapturingChat(self)


class _UsageCapturingChat:
    def __init__(self, outer: _UsageCapturingClient):
        self.completions = _UsageCapturingCompletions(outer)


class _UsageCapturingCompletions:
    def __init__(self, outer: _UsageCapturingClient):
        self.outer = outer

    async def create(self, *args, **kwargs):
        response = await self.outer.inner.chat.completions.create(*args, **kwargs)
        usage = getattr(response, "usage", None)
        prompt_tokens, completion_tokens = _extract_usage(usage)
        self.outer.prompt_tokens += prompt_tokens
        self.outer.completion_tokens += completion_tokens
        return response


def _extract_usage(usage: Any) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("prompt_tokens", 0) or 0), int(
            usage.get("completion_tokens", 0) or 0
        )
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "completion_tokens", 0) or 0
    )
