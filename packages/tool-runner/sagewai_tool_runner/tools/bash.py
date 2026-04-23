"""bash tool — run a shell command under timeout, capture stdout/stderr."""
from __future__ import annotations

import asyncio
import time
from typing import Any


async def run_bash(args: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, str) or not command:
        return {
            "ok": False,
            "error": "bash requires a non-empty 'command' string argument",
        }
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "duration_ms": duration_ms,
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": f"timeout after {timeout_s}s",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
