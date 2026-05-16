# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AclFilteringSandboxHandle — per-tool Tier-2 secret allowlist filter
applied at the host-side RPC boundary. Sealed-iii.D.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import SandboxStats, ToolCall, ToolResult
from sagewai.sealed.acl import compute_allowed_env

if TYPE_CHECKING:
    from sagewai.sealed.audit import AuditWriter


class AclFilteringSandboxHandle:
    """Decorator: filter env per-tool-call before delegating to ``inner``."""

    def __init__(
        self,
        inner: SandboxHandle,
        *,
        secret_keys: set[str],
        acl: dict[str, list[str]],
        audit_writer: AuditWriter,
        run_id: str,
        profile_id: str | None,
    ) -> None:
        self._inner = inner
        self._secret_keys = set(secret_keys)
        self._acl = dict(acl)
        self._audit = audit_writer
        self._run_id = run_id
        self._profile_id = profile_id
        self._full_env: dict[str, str] = {}

    # SandboxHandle Protocol attributes — pass-through
    @property
    def sandbox_id(self) -> str:
        return self._inner.sandbox_id

    @property
    def mode(self):
        return self._inner.mode

    @property
    def image(self) -> str:
        return self._inner.image

    @property
    def image_digest(self) -> str:
        return self._inner.image_digest

    async def set_env(self, env: dict[str, str]) -> None:
        self._full_env = dict(env)
        await self._inner.set_env(env)

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        filtered, removed = compute_allowed_env(
            full_env=self._full_env,
            secret_keys=self._secret_keys,
            acl=self._acl,
            tool_name=tool_call.tool,
        )

        if removed:
            try:
                await self._audit.emit(
                    event_type="acl.enforced",
                    actor_type="runtime",
                    profile_id=self._profile_id,
                    run_id=self._run_id,
                    details={
                        "tool": tool_call.tool,
                        "allowed_keys": sorted(set(filtered) & self._secret_keys),
                        "removed_keys": removed,
                        "acl_source": "profile",
                    },
                )
            except Exception:
                pass
        elif tool_call.tool not in self._acl:
            try:
                await self._audit.emit(
                    event_type="acl.passthrough",
                    actor_type="runtime",
                    profile_id=self._profile_id,
                    run_id=self._run_id,
                    details={"tool": tool_call.tool},
                )
            except Exception:
                pass

        # Swap to filtered env, exec, restore.
        await self._inner.set_env(filtered)
        try:
            return await self._inner.exec(tool_call)
        finally:
            await self._inner.set_env(self._full_env)

    async def copy_in(self, src: Path, dst: PurePosixPath) -> None:
        await self._inner.copy_in(src, dst)

    async def copy_out(self, src: PurePosixPath, dst: Path) -> None:
        await self._inner.copy_out(src, dst)

    async def stats(self) -> SandboxStats:
        return await self._inner.stats()

    async def stop(self, *, timeout: float = 10.0) -> None:
        await self._inner.stop(timeout=timeout)
