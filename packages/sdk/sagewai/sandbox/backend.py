# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SandboxBackend / SandboxHandle protocols."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

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
from sagewai.sandbox.pool_protocol import PoolStrategy


@runtime_checkable
class SandboxHandle(Protocol):
    """Opaque reference to a live sandbox. Returned by backend.start()."""

    sandbox_id: str
    mode: SandboxMode
    image: str
    image_digest: str

    async def exec(self, tool_call: ToolCall) -> ToolResult: ...
    async def set_env(self, env: dict[str, str]) -> None: ...
    async def copy_in(self, src: Path, dst: PurePosixPath) -> None: ...
    async def copy_out(self, src: PurePosixPath, dst: Path) -> None: ...
    async def stats(self) -> SandboxStats: ...
    async def stop(self, *, timeout: float = 10.0) -> None: ...


@runtime_checkable
class SandboxBackend(Protocol):
    """Pluggable backend interface.

    OSS default: DockerBackend.
    Enterprise: FirecrackerBackend, GVisorBackend, HostedE2BBackend.
    """

    name: str
    pool_strategy: PoolStrategy

    async def health_check(self) -> BackendHealth: ...

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
    ) -> SandboxHandle: ...

    async def reap(self, *, older_than: timedelta) -> int: ...
