# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-aware tool execution for autopilot agents.

:class:`SealedToolRunner` wraps async tool callables and enforces the
Sealed credential binding contract:

- If a tool requires Sealed scopes (per :mod:`sagewai.autopilot.tool_scopes`)
  and a matching :class:`~sagewai.autopilot.sealed_matcher.ProfileRecord` is
  provided, the profile's environment is injected into the tool call via a
  ``_env`` key in *args*.

- If a tool requires scopes but no profile is provided, a
  :class:`JitHitlPendingError` is raised — the step enters the JIT-HITL
  (Just-In-Time Human-In-The-Loop) approval flow instead of executing.

- If a tool has no scope requirements, it executes regardless of whether
  a profile is present.

This design means the sealed binding is a pure pre-execution check.
The actual credential delivery (writing secrets to the environment of the
subprocess or container where the tool runs) is delegated to the tool
callable itself, which receives ``_env`` in its *args* dict and may use
those values however appropriate.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import logging

from sagewai.autopilot.errors import AutopilotError
from sagewai.autopilot.sealed_matcher import ProfileRecord
from sagewai.autopilot.tool_scopes import get_scopes

logger = logging.getLogger(__name__)

ToolCallable = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


class JitHitlPendingError(AutopilotError):
    """Raised when a tool requires Sealed scopes but no profile is bound.

    The agent step is placed in a JIT-HITL (Just-In-Time Human-In-The-Loop)
    pending state; execution resumes once an operator assigns a profile via
    the ``sealed-override`` admin endpoint.

    Attributes:
        tool_name:      Name of the tool that triggered the error.
        step_id:        Identifier of the agent step (optional).
        required_scopes: Scopes the tool requires that are unmet.
    """

    def __init__(
        self,
        tool_name: str,
        *,
        step_id: str | None = None,
        required_scopes: frozenset[str],
    ) -> None:
        self.tool_name = tool_name
        self.step_id = step_id
        self.required_scopes = required_scopes
        super().__init__(
            f"JIT-HITL: tool {tool_name!r} requires scopes {sorted(required_scopes)!r} "
            f"but no Sealed profile is bound to step {step_id!r}"
        )


class SealedToolRunner:
    """Sealed-aware executor for a fixed set of async tool callables.

    Args:
        tools:       Mapping of tool name → async callable.
        profile:     Matched :class:`ProfileRecord` providing credentials,
                     or ``None`` if no profile was matched.
        profile_env: Env dict to inject; typically derived from the profile's
                     secrets. Defaults to empty dict.
        step_id:     Identifier of the agent step; used in error messages.
    """

    def __init__(
        self,
        *,
        tools: dict[str, ToolCallable],
        profile: ProfileRecord | None,
        profile_env: dict[str, str] | None = None,
        step_id: str | None = None,
    ) -> None:
        self._tools = tools
        self.profile = profile
        self._profile_env = profile_env or {}
        self.step_id = step_id

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute *tool_name* with *args* after Sealed scope check.

        Raises:
            KeyError:           Tool not registered.
            JitHitlPendingError: Tool requires scopes but no profile is bound.
        """
        fn = self._tools[tool_name]  # KeyError if not registered

        required = get_scopes(tool_name)
        if required and self.profile is None:
            raise JitHitlPendingError(
                tool_name,
                step_id=self.step_id,
                required_scopes=required,
            )

        call_args = dict(args)
        if self._profile_env:
            call_args["_env"] = self._profile_env

        if self.profile is not None:
            logger.debug(
                "sealed.bind: tool=%r profile=%r step=%r",
                tool_name,
                self.profile.id,
                self.step_id,
            )

        return await fn(call_args)
