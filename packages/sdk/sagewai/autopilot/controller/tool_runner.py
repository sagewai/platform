# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sandbox-aware tool execution for autopilot agents.

:class:`ToolRunner` wraps a dict of async callables and enforces a
per-runner :class:`~sagewai.autopilot.tool_risk_profile.SandboxTier`
ceiling. Calls to tools whose risk tier exceeds the runner's allowed tier
raise :class:`SandboxViolationError` before the tool is invoked — the
tool never executes.

Default allowed tier is ``UNTRUSTED`` (most permissive) so that runners
explicitly configured for stricter environments must opt in by passing a
lower tier. Callers that don't care about sandboxing can use the default
and all registered tools will be allowed.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from sagewai.autopilot.errors import AutopilotError
from sagewai.autopilot.tool_risk_profile import SandboxTier, get_tier

ToolCallable = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


class SandboxViolationError(AutopilotError):
    """Raised when a tool call exceeds the runner's allowed sandbox tier.

    Attributes:
        tool_name:     Name of the tool that was blocked.
        required_tier: Tier the tool requires.
        allowed_tier:  Tier the runner permits.
    """

    def __init__(
        self,
        tool_name: str,
        *,
        required_tier: SandboxTier,
        allowed_tier: SandboxTier,
    ) -> None:
        self.tool_name = tool_name
        self.required_tier = required_tier
        self.allowed_tier = allowed_tier
        super().__init__(
            f"tool {tool_name!r} requires tier {required_tier.name!r} but runner "
            f"only allows {allowed_tier.name!r}"
        )


class ToolRunner:
    """Sandbox-aware executor for a fixed set of async tool callables.

    Args:
        tools:        Mapping of tool name → async callable.
        allowed_tier: Maximum (most permissive) sandbox tier this runner
                      will execute. Tools that require a higher (more
                      restrictive) tier are blocked with
                      :class:`SandboxViolationError`. Defaults to
                      ``SandboxTier.UNTRUSTED`` (all tools allowed).
    """

    def __init__(
        self,
        *,
        tools: dict[str, ToolCallable],
        allowed_tier: SandboxTier = SandboxTier.UNTRUSTED,
    ) -> None:
        self._tools = tools
        self.allowed_tier = allowed_tier

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute *tool_name* with *args* after sandbox tier check.

        Raises:
            KeyError:              Tool not registered.
            SandboxViolationError: Tool's tier exceeds allowed_tier.
        """
        fn = self._tools[tool_name]  # KeyError if not registered

        required = get_tier(tool_name)
        if required > self.allowed_tier:
            raise SandboxViolationError(
                tool_name,
                required_tier=required,
                allowed_tier=self.allowed_tier,
            )

        return await fn(args)
