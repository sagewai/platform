# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""NullBackend — runs tool calls in-process with scoped cwd and env scrub.

Used for mode=none: local development, trusted single-tenant. Provides no
isolation, but still enforces a clean scratch directory and env-var scrub
so tools cannot accidentally read host environment variables.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path, PurePosixPath

from sagewai.sandbox.models import (
    BackendHealth,
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    SandboxMode,
    SandboxStats,
    ToolCall,
    ToolResult,
)


class NullSandboxHandle:
    """Handle for a NullBackend sandbox."""

    def __init__(self, sandbox_id: str, env: Mapping[str, str], workdir: Path | None) -> None:
        self.sandbox_id = sandbox_id
        self.mode = SandboxMode.NONE
        self.image = "null"
        self.image_digest = ""
        self._env = dict(env)
        self._workdir = workdir

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        if tool_call.tool != "bash":
            return ToolResult(
                call_id=tool_call.call_id,
                ok=False,
                error=f"NullBackend supports only tool=bash, got {tool_call.tool!r}",
            )
        command = tool_call.args.get("command", "")
        if not isinstance(command, str) or not command:
            return ToolResult(
                call_id=tool_call.call_id,
                ok=False,
                error="bash tool requires a 'command' string argument",
            )
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,                       # no os.environ passthrough
                cwd=str(self._workdir) if self._workdir else None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=tool_call.timeout_s
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            return ToolResult(
                call_id=tool_call.call_id,
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                duration_ms=duration_ms,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                call_id=tool_call.call_id,
                ok=False,
                error=f"timeout after {tool_call.timeout_s}s",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def copy_in(self, src: Path, dst: PurePosixPath) -> None:
        raise NotImplementedError("NullBackend does not support copy_in")

    async def copy_out(self, src: PurePosixPath, dst: Path) -> None:
        raise NotImplementedError("NullBackend does not support copy_out")

    async def stats(self) -> SandboxStats:
        return SandboxStats()

    async def stop(self, *, timeout: float = 10.0) -> None:
        return None


class NullBackend:
    """In-process backend for mode=none."""

    name = "null"

    async def health_check(self) -> BackendHealth:
        return BackendHealth(ok=True, backend="null", detail="in-process")

    async def start(
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        image_digest: str,
        env: Mapping[str, str],
        network_policy: NetworkPolicy,
        resource_limits: ResourceLimits,
        workdir_mount: Path | None,
        lifetime: SandboxLifetime,
    ) -> NullSandboxHandle:
        sandbox_id = f"null-{uuid.uuid4().hex[:12]}"
        if workdir_mount:
            workdir_mount.mkdir(parents=True, exist_ok=True)
        return NullSandboxHandle(sandbox_id, env, workdir_mount)

    async def reap(self, *, older_than: timedelta) -> int:
        return 0
