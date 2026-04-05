# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tiered permission policy for agent tool execution.

Implements prefix-based and exact-match tool filtering with
permission levels, inspired by Claude Code's permission system.

Usage::

    from sagewai.safety.permissions import PermissionPolicy, PermissionLevel

    policy = PermissionPolicy(
        default_level=PermissionLevel.AUTO_APPROVE,
        deny_prefixes=["bash", "shell"],
        deny_names=["delete_all", "drop_table"],
        suggest_prefixes=["file_write"],
    )

    result = policy.check("bash_exec", PermissionLevel.AUTO_APPROVE)
    # result.allowed == False, result.reason == "Tool denied by prefix: bash"
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class PermissionLevel(enum.IntEnum):
    """Permission levels in ascending order of trust."""

    NONE = 0
    READ = 1
    SUGGEST = 2
    AUTO_APPROVE = 3
    ADMIN = 4


@dataclass
class PermissionCheckResult:
    """Result of a permission check."""

    allowed: bool
    level: PermissionLevel
    reason: str = ""
    needs_approval: bool = False  # True when level is SUGGEST


@runtime_checkable
class PermissionPrompter(Protocol):
    """Protocol for requesting user approval for SUGGEST-level tools."""

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> bool:
        """Ask for approval. Returns True if approved."""
        ...


class CLIPrompter:
    """Interactive terminal prompter for SUGGEST-level approvals."""

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> bool:
        import asyncio

        prompt = (
            f"Tool '{tool_name}' requires approval. {reason}\n"
            "Allow? [y/N]: "
        )
        response = await asyncio.to_thread(input, prompt)
        return response.strip().lower() in ("y", "yes")


class ScriptedPrompter:
    """Predetermined responses for testing."""

    def __init__(
        self,
        responses: dict[str, bool] | None = None,
        *,
        default: bool = False,
    ) -> None:
        self._responses = responses or {}
        self._default = default

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> bool:
        return self._responses.get(tool_name, self._default)


class PermissionPolicy:
    """Tiered permission policy with prefix and exact-match filtering.

    Parameters
    ----------
    default_level:
        Default permission level for tools not explicitly configured.
    deny_prefixes:
        Tool name prefixes that are always denied
        (e.g., ``"bash"`` denies ``"bash_exec"``).
    deny_names:
        Exact tool names that are always denied.
    suggest_prefixes:
        Tool name prefixes that require SUGGEST-level approval.
    suggest_names:
        Exact tool names that require SUGGEST-level approval.
    tool_levels:
        Explicit per-tool permission level overrides.
    prompter:
        Prompter for SUGGEST-level approvals.
        If ``None``, SUGGEST tools are denied.
    """

    def __init__(
        self,
        *,
        default_level: PermissionLevel = PermissionLevel.AUTO_APPROVE,
        deny_prefixes: list[str] | None = None,
        deny_names: list[str] | None = None,
        suggest_prefixes: list[str] | None = None,
        suggest_names: list[str] | None = None,
        tool_levels: dict[str, PermissionLevel] | None = None,
        prompter: PermissionPrompter | None = None,
    ) -> None:
        self.default_level = default_level
        self.deny_prefixes = deny_prefixes or []
        self.deny_names = set(deny_names or [])
        self.suggest_prefixes = suggest_prefixes or []
        self.suggest_names = set(suggest_names or [])
        self.tool_levels = tool_levels or {}
        self.prompter = prompter

    def check(
        self,
        tool_name: str,
        current_level: PermissionLevel | None = None,
    ) -> PermissionCheckResult:
        """Check if a tool is allowed at the given permission level.

        Args:
            tool_name: Name of the tool to check.
            current_level: Current agent's permission level.
                Defaults to ``self.default_level``.
        """
        level = current_level or self.default_level

        # Exact deny match
        if tool_name in self.deny_names:
            return PermissionCheckResult(
                allowed=False,
                level=level,
                reason=f"Tool denied by name: {tool_name}",
            )

        # Prefix deny match
        for prefix in self.deny_prefixes:
            if tool_name.startswith(prefix):
                return PermissionCheckResult(
                    allowed=False,
                    level=level,
                    reason=f"Tool denied by prefix: {prefix}",
                )

        # Check explicit tool level
        if tool_name in self.tool_levels:
            required = self.tool_levels[tool_name]
            if level < required:
                return PermissionCheckResult(
                    allowed=False,
                    level=level,
                    reason=(
                        f"Tool requires {required.name}, "
                        f"current level is {level.name}"
                    ),
                )

        # Suggest match — needs approval
        if tool_name in self.suggest_names:
            return PermissionCheckResult(
                allowed=True,
                level=level,
                needs_approval=True,
                reason=f"Tool requires approval: {tool_name}",
            )
        for prefix in self.suggest_prefixes:
            if tool_name.startswith(prefix):
                return PermissionCheckResult(
                    allowed=True,
                    level=level,
                    needs_approval=True,
                    reason=f"Tool requires approval (prefix: {prefix})",
                )

        # Default: allowed
        return PermissionCheckResult(allowed=True, level=level)

    async def check_and_approve(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        current_level: PermissionLevel | None = None,
    ) -> PermissionCheckResult:
        """Check permission and request approval if needed."""
        result = self.check(tool_name, current_level)

        if not result.allowed:
            return result

        if result.needs_approval:
            if self.prompter is None:
                return PermissionCheckResult(
                    allowed=False,
                    level=result.level,
                    reason=(
                        "No prompter configured for "
                        f"SUGGEST-level tool: {tool_name}"
                    ),
                )
            approved = await self.prompter.request_approval(
                tool_name, arguments, result.reason
            )
            if not approved:
                return PermissionCheckResult(
                    allowed=False,
                    level=result.level,
                    reason=f"User denied approval for: {tool_name}",
                )
            # Approval granted — clear the flag so callers see it as resolved
            result.needs_approval = False

        return result
