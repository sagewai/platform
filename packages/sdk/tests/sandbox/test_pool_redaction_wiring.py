# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pool acquire wraps the inner handle in RedactingSandboxHandle."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
    SandboxStats,
    ToolCall,
    ToolResult,
)
from sagewai.sandbox.redacting_handle import RedactingSandboxHandle


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        from sagewai.sandbox.pool_protocol import PoolStrategy
        self.pool_strategy = PoolStrategy.LOCAL_CACHE

    async def health_check(self):
        from sagewai.sandbox.models import BackendHealth
        return BackendHealth(ok=True, backend="fake")

    async def start(self, **kwargs: Any):
        return _FakeHandle()

    async def probe_runner(self, handle):
        return "1.0.0"

    async def reap(self, *, older_than):
        return 0


class _FakeHandle:
    sandbox_id = "sgw-fake"
    mode = SandboxMode.PER_RUN
    image = "ghcr.io/test/img:dev"
    image_digest = "sha256:abc"

    def __init__(self) -> None:
        self.set_env_calls: list[dict[str, str]] = []

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(call_id=tool_call.call_id, ok=True, stdout="ok")

    async def set_env(self, env: dict[str, str]) -> None:
        self.set_env_calls.append(env)

    async def copy_in(self, src, dst): ...
    async def copy_out(self, src, dst): ...
    async def stats(self) -> SandboxStats: return SandboxStats()
    async def stop(self, *, timeout: float = 10.0) -> None: ...


class FakeAuditWriter:
    def __init__(self) -> None: self.events: list[dict] = []
    async def emit(self, **kwargs: Any) -> None: self.events.append(kwargs)


class FakeProvider:
    def __init__(self) -> None:
        self._audit = FakeAuditWriter()

    async def env_for(self, **kwargs: Any) -> dict[str, str]:
        # Return one secret + one knob; pool builds redactor from these
        return {"OPENAI_API_KEY": "sk-aaaaaaaaaaaaaaaaaaaaa", "DEBUG": "1"}


@pytest.mark.asyncio
async def test_pool_wraps_handle_in_redacting_when_secrets_present(tmp_path: Path) -> None:
    pool = LocalCacheSandboxPool(
        backend=FakeBackend(),
        config=SandboxConfig(
            mode=SandboxMode.PER_RUN,
            network_policy=NetworkPolicy.NONE,
        ),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=FakeProvider(),
        audit_writer=FakeAuditWriter(),
    )

    async with pool.acquire(
        project_id="p-1",
        run_id="r-1",
        execution_mode=ExecutionMode.IDENTITY,
        image="img",
        image_digest="sha256:abc",
        image_variant=SandboxImageVariant.BASE,
        security_profile_ref="acme-prod",
        effective_env_keys=["OPENAI_API_KEY", "DEBUG"],
        effective_secret_keys=["OPENAI_API_KEY"],
    ) as handle:
        assert isinstance(handle, RedactingSandboxHandle)
        assert handle.sandbox_id == "sgw-fake"
        result = await handle.exec(ToolCall(tool="shell", args={}, call_id="c"))
        assert result.ok
