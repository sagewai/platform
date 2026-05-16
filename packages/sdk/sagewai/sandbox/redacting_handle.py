# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""RedactingSandboxHandle — wraps any SandboxHandle, scrubs secret values
out of ToolResult.stdout/stderr/error before returning to the host.

Sealed-iii.B insertion point. Backend-agnostic via the Plan 1.5 Protocol.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import SandboxStats, ToolCall, ToolResult

if TYPE_CHECKING:
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.redaction import Redactor


class RedactingSandboxHandle:
    """Decorator: ``inner`` SandboxHandle + post-exec redaction."""

    def __init__(
        self,
        inner: SandboxHandle,
        *,
        redactor: Redactor,
        audit_writer: AuditWriter,
        run_id: str,
        profile_id: str | None,
    ) -> None:
        self._inner = inner
        self._redactor = redactor
        self._audit = audit_writer
        self._run_id = run_id
        self._profile_id = profile_id

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

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        result = await self._inner.exec(tool_call)
        return await self._redact_result(result, tool_call.tool)

    async def set_env(self, env: dict[str, str]) -> None:
        await self._inner.set_env(env)

    async def copy_in(self, src: Path, dst: PurePosixPath) -> None:
        await self._inner.copy_in(src, dst)

    async def copy_out(self, src: PurePosixPath, dst: Path) -> None:
        await self._inner.copy_out(src, dst)

    async def stats(self) -> SandboxStats:
        return await self._inner.stats()

    async def stop(self, *, timeout: float = 10.0) -> None:
        await self._inner.stop(timeout=timeout)

    async def _redact_result(self, result: ToolResult, tool_name: str) -> ToolResult:
        if self._redactor.value_count == 0:
            return result

        new_stdout = result.stdout
        if result.stdout:
            new_stdout = await self._redactor.redact_and_audit(
                result.stdout,
                surface="stdout",
                audit_writer=self._audit,
                run_id=self._run_id,
                profile_id=self._profile_id,
                tool_name=tool_name,
            )

        new_stderr = result.stderr
        if result.stderr:
            new_stderr = await self._redactor.redact_and_audit(
                result.stderr,
                surface="stderr",
                audit_writer=self._audit,
                run_id=self._run_id,
                profile_id=self._profile_id,
                tool_name=tool_name,
            )

        new_error = result.error
        if result.error:
            new_error = await self._redactor.redact_and_audit(
                result.error,
                surface="error",
                audit_writer=self._audit,
                run_id=self._run_id,
                profile_id=self._profile_id,
                tool_name=tool_name,
            )

        return result.model_copy(update={
            "stdout": new_stdout,
            "stderr": new_stderr,
            "error": new_error,
        })
