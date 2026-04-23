# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SandboxedToolDispatcher — issues ToolCall objects against a SandboxHandle."""
from __future__ import annotations

from typing import Any

from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import ToolCall, ToolResult


class SandboxedToolDispatcher:
    """Adapter between DurableWorkflow tool invocations and SandboxHandle.exec."""

    def __init__(self, handle: SandboxHandle) -> None:
        self._handle = handle

    async def run(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        call_id: str,
        timeout_s: float = 60.0,
    ) -> ToolResult:
        return await self._handle.exec(
            ToolCall(tool=tool, args=args, call_id=call_id, timeout_s=timeout_s)
        )
