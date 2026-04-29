# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Pool acquire wraps in order: ACL outermost, Redacting inner, real handle."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.acl_handle import AclFilteringSandboxHandle
from sagewai.sandbox.local_cache_pool import LocalCacheSandboxPool
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxConfig,
    SandboxImageVariant,
    SandboxMode,
    SandboxStats,
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

    async def start(self, **kwargs: Any): return _H()
    async def probe_runner(self, h): return "1.0.0"
    async def reap(self, *, older_than): return 0


class _H:
    sandbox_id = "sgw-x"
    mode = SandboxMode.PER_RUN
    image = "img"
    image_digest = "sha256:x"

    async def exec(self, c): return ToolResult(call_id=c.call_id, ok=True)
    async def set_env(self, env): ...
    async def copy_in(self, s, d): ...
    async def copy_out(self, s, d): ...
    async def stats(self): return SandboxStats()
    async def stop(self, *, timeout=10.0): ...


class FakeAudit:
    async def emit(self, **k): ...


class FakeProvider:
    def __init__(self) -> None:
        self._audit = FakeAudit()

    async def env_for(self, **kwargs):
        # Return env value with an ACL pre-resolved
        return {"K1": "sk-aaaaaaaaaaaaaaaaaaaaaa", "DEBUG": "1"}

    async def cleanup_run(self, **kwargs):
        from sagewai.sandbox.pool_protocol import CleanupResult
        return CleanupResult(error=None)


@pytest.mark.asyncio
async def test_pool_composes_acl_outside_redactor(tmp_path: Path) -> None:
    pool = LocalCacheSandboxPool(
        backend=FakeBackend(),
        config=SandboxConfig(mode=SandboxMode.PER_RUN, network_policy=NetworkPolicy.NONE),
        worker_id="w-1",
        scratch_root=tmp_path,
        sealed_secret_provider=FakeProvider(),
        audit_writer=FakeAudit(),
    )

    async with pool.acquire(
        project_id="p", run_id="r", execution_mode=ExecutionMode.IDENTITY,
        image="img", image_digest="sha256:x", image_variant=SandboxImageVariant.BASE,
        security_profile_ref="acme",
        effective_env_keys=["K1", "DEBUG"],
        effective_secret_keys=["K1"],
        acl={"claude-code": ["K1"]},   # NEW kwarg
    ) as handle:
        # Outermost should be Acl wrapper
        assert isinstance(handle, AclFilteringSandboxHandle)
        # Its inner should be Redacting wrapper
        assert isinstance(handle._inner, RedactingSandboxHandle)
