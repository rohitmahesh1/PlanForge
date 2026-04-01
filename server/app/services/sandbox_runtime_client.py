from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from app.services.tool_host import ToolHost


class SandboxRuntimeClient:
    """
    Thin NDJSON client for the JS sandbox sidecar.

    The current sidecar is a protocol-compatible Node runner that is ready to
    be swapped to a QuickJS/WASM engine internally without changing the Python
    host contract.
    """

    def __init__(
        self,
        tool_host: ToolHost,
        *,
        command: Optional[list[str]] = None,
        timeout_ms: Optional[int] = None,
    ):
        self.tool_host = tool_host
        self.command = command or self._default_command()
        self.timeout_ms = timeout_ms or int(os.getenv("SANDBOX_TIMEOUT_MS", "4000"))
        self.repo_root = Path(__file__).resolve().parents[3]

    async def run_plan(
        self,
        *,
        plan: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        limits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = uuid4().hex
        stderr_chunks: list[str] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.repo_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return _error_payload(f"Sandbox runtime command not found: {exc}")
        except Exception as exc:
            return _error_payload(f"Failed to start sandbox runtime: {type(exc).__name__}: {exc}")

        stderr_task = asyncio.create_task(_drain_stream(proc.stderr, stderr_chunks))

        try:
            await _write_json(
                proc.stdin,
                {
                    "type": "run_plan",
                    "session_id": session_id,
                    "plan": plan,
                    "context": context or {},
                    "limits": limits or {},
                },
            )

            return await asyncio.wait_for(
                self._conversation(proc, session_id=session_id),
                timeout=self.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            return _error_payload(f"Sandbox runtime timed out after {self.timeout_ms}ms")
        finally:
            await _terminate_process(proc)
            await stderr_task

    async def _conversation(self, proc: asyncio.subprocess.Process, *, session_id: str) -> Dict[str, Any]:
        if proc.stdout is None:
            return _error_payload("Sandbox runtime stdout is unavailable")

        while True:
            raw_line = await proc.stdout.readline()
            if not raw_line:
                return _error_payload("Sandbox runtime exited before returning a result")

            line = raw_line.decode("utf-8").strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                return _error_payload(f"Sandbox runtime returned invalid JSON: {exc}")

            if message.get("session_id") != session_id:
                continue

            msg_type = message.get("type")
            if msg_type == "tool_call":
                await self._handle_tool_call(proc, message)
                continue

            if msg_type == "done":
                payload = message.get("payload")
                if isinstance(payload, dict):
                    return payload
                return _error_payload("Sandbox runtime returned an invalid completion payload")

            if msg_type == "error":
                return _error_payload(str(message.get("error") or "Sandbox runtime error"))

            return _error_payload(f"Sandbox runtime returned unsupported message type: {msg_type}")

    async def _handle_tool_call(
        self,
        proc: asyncio.subprocess.Process,
        message: Dict[str, Any],
    ) -> None:
        call_id = str(message.get("call_id") or "")
        tool_name = str(message.get("tool") or "")
        args = message.get("args")
        if not isinstance(args, dict):
            args = {}

        try:
            result = await self.tool_host.execute(tool_name, args)
            response = {
                "type": "tool_result",
                "session_id": message.get("session_id"),
                "call_id": call_id,
                "ok": True,
                "result": result,
            }
        except Exception as exc:
            response = {
                "type": "tool_result",
                "session_id": message.get("session_id"),
                "call_id": call_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        await _write_json(proc.stdin, response)

    def _default_command(self) -> list[str]:
        raw = os.getenv("SANDBOX_RUNTIME_COMMAND", "node sandbox/quickjs/runner.js")
        return shlex.split(raw)


async def _write_json(
    stream: Optional[asyncio.StreamWriter],
    payload: Dict[str, Any],
) -> None:
    if stream is None or stream.is_closing():
        raise RuntimeError("Sandbox runtime stdin is unavailable")
    stream.write((json.dumps(payload) + "\n").encode("utf-8"))
    await stream.drain()


async def _drain_stream(stream: Optional[asyncio.StreamReader], sink: list[str]) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        sink.append(line.decode("utf-8", errors="replace"))


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None and not proc.stdin.is_closing():
        proc.stdin.close()

    if proc.returncode is not None:
        await proc.wait()
        return

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=1)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


def _error_payload(message: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "trace": [],
        "op_ids": [],
        "error": message,
    }
