# server/app/llm_contract/__init__.py
"""
Helpers for loading the LLM tool schemas and standard prompts.
"""
from __future__ import annotations
from importlib.resources import files
import json
from typing import Any

def load_tool_schemas() -> dict[str, Any]:
    data = files(__package__).joinpath("tool_schemas.json").read_text(encoding="utf-8")
    return json.loads(data)

def load_system_prompt() -> str:
    return files(__package__).joinpath("system_prompt.md").read_text(encoding="utf-8")
