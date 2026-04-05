# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Pre/post tool execution hooks for agent tool calls.

Hooks allow intercepting tool execution with allow/deny/modify semantics.
Inspired by Claude Code's hook system with exit-code semantics.

Usage::

    from sagewai.core.hooks import HookRunner, HookResult

    runner = HookRunner()

    @runner.pre_tool
    async def audit_hook(context: HookContext) -> HookResult:
        logger.info("Tool %s called with %s", context.tool_name, context.arguments)
        return HookResult.allow()

    @runner.pre_tool
    async def deny_dangerous(context: HookContext) -> HookResult:
        if context.tool_name.startswith("delete_"):
            return HookResult.deny("Deletion tools are blocked")
        return HookResult.allow()
"""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)


class HookAction(enum.Enum):
    """Action returned by a hook callback."""

    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass
class HookContext:
    """Context passed to hook callbacks."""

    tool_name: str
    arguments: dict[str, Any]
    agent_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Result returned from a hook callback."""

    action: HookAction
    message: str = ""
    modified_arguments: dict[str, Any] | None = None
    modified_result: str | None = None

    @classmethod
    def allow(cls) -> HookResult:
        """Allow tool execution to proceed."""
        return cls(action=HookAction.ALLOW)

    @classmethod
    def deny(cls, message: str = "Tool execution denied by hook") -> HookResult:
        """Deny tool execution with a reason message."""
        return cls(action=HookAction.DENY, message=message)

    @classmethod
    def modify_args(cls, arguments: dict[str, Any]) -> HookResult:
        """Modify tool arguments before execution."""
        return cls(action=HookAction.MODIFY, modified_arguments=arguments)

    @classmethod
    def modify_result(cls, result: str) -> HookResult:
        """Modify tool result after execution."""
        return cls(action=HookAction.MODIFY, modified_result=result)


# Type for hook callbacks — supports both sync and async functions.
HookCallback = Callable[[HookContext], Union[Awaitable[HookResult], HookResult]]


class HookRunner:
    """Manages pre-tool and post-tool hooks for agent tool execution.

    Hooks are executed in registration order. Pre-tool hooks can:
    - Allow execution (default)
    - Deny execution (returns error to LLM)
    - Modify arguments before execution

    Post-tool hooks can:
    - Allow result through (default)
    - Modify the result before returning to LLM

    If any pre-tool hook denies, execution stops immediately.
    """

    def __init__(self) -> None:
        self._pre_hooks: list[HookCallback] = []
        self._post_hooks: list[HookCallback] = []

    def pre_tool(self, fn: HookCallback) -> HookCallback:
        """Decorator to register a pre-tool hook."""
        self._pre_hooks.append(fn)
        return fn

    def post_tool(self, fn: HookCallback) -> HookCallback:
        """Decorator to register a post-tool hook."""
        self._post_hooks.append(fn)
        return fn

    def add_pre_hook(self, callback: HookCallback) -> None:
        """Register a pre-tool hook programmatically."""
        self._pre_hooks.append(callback)

    def add_post_hook(self, callback: HookCallback) -> None:
        """Register a post-tool hook programmatically."""
        self._post_hooks.append(callback)

    async def run_pre_hooks(self, context: HookContext) -> HookResult:
        """Run all pre-tool hooks in order.

        Returns the first deny result immediately.  Modify results accumulate
        (last modify wins for arguments).  Returns allow if all hooks pass
        without modifications.
        """
        current_args = dict(context.arguments)

        for hook in self._pre_hooks:
            ctx = HookContext(
                tool_name=context.tool_name,
                arguments=current_args,
                agent_name=context.agent_name,
                metadata=context.metadata,
            )
            try:
                result = hook(ctx)
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    result = await result
            except Exception:  # noqa: broad-exception-caught — hook resilience
                logger.exception(
                    "Pre-tool hook failed for %s", context.tool_name
                )
                continue

            if result.action == HookAction.DENY:
                return result
            if (
                result.action == HookAction.MODIFY
                and result.modified_arguments is not None
            ):
                current_args = result.modified_arguments

        if current_args != context.arguments:
            return HookResult.modify_args(current_args)
        return HookResult.allow()

    async def run_post_hooks(
        self, context: HookContext, tool_result: str
    ) -> HookResult:
        """Run all post-tool hooks in order.

        Post hooks can modify the result string.  Returns allow if no
        modifications were made.
        """
        current_result = tool_result

        for hook in self._post_hooks:
            ctx = HookContext(
                tool_name=context.tool_name,
                arguments=context.arguments,
                agent_name=context.agent_name,
                metadata={**context.metadata, "result": current_result},
            )
            try:
                result = hook(ctx)
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    result = await result
            except Exception:  # noqa: broad-exception-caught — hook resilience
                logger.exception(
                    "Post-tool hook failed for %s", context.tool_name
                )
                continue

            if (
                result.action == HookAction.MODIFY
                and result.modified_result is not None
            ):
                current_result = result.modified_result

        if current_result != tool_result:
            return HookResult.modify_result(current_result)
        return HookResult.allow()

    @property
    def has_hooks(self) -> bool:
        """Return True if any hooks are registered."""
        return bool(self._pre_hooks or self._post_hooks)
