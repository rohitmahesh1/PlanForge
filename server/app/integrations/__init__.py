# server/app/integrations/__init__.py
from __future__ import annotations

from importlib import import_module
from typing import Any


def _load_router(module_name: str) -> Any | None:
    try:
        module = import_module(f"{__name__}.{module_name}")
    except ModuleNotFoundError:
        return None
    return getattr(module, "router", None)


telegram_router = _load_router("telegram")
twilio_router = _load_router("twilio")

__all__ = ["telegram_router", "twilio_router"]
