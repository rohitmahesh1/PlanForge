from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional


def estimate_openai_cost_usd(
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    pricing: Optional[Dict[str, Any]] = None,
) -> float:
    input_rate = _resolve_rate(
        model=model,
        direction="input",
        pricing=pricing,
    )
    output_rate = _resolve_rate(
        model=model,
        direction="output",
        pricing=pricing,
    )
    if input_rate is None or output_rate is None:
        return 0.0
    return ((tokens_in / 1_000_000) * input_rate) + ((tokens_out / 1_000_000) * output_rate)


def _resolve_rate(
    *,
    model: str,
    direction: str,
    pricing: Optional[Dict[str, Any]],
) -> Optional[float]:
    if pricing:
        direct_key = f"{direction}_per_1m"
        if direct_key in pricing:
            return float(pricing[direct_key])
        models = pricing.get("models")
        if isinstance(models, dict):
            model_entry = models.get(model)
            if isinstance(model_entry, dict) and direct_key in model_entry:
                return float(model_entry[direct_key])

    model_key = _sanitize_model_key(model)
    env_keys = [
        f"EVAL_OPENAI_PRICE_{model_key}_{direction.upper()}_PER_1M",
        f"EVAL_OPENAI_PRICE_{direction.upper()}_PER_1M",
    ]
    for env_key in env_keys:
        raw = os.getenv(env_key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def _sanitize_model_key(model: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", model.upper()).strip("_")
