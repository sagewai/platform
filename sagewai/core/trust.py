# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Trust-gated agent initialization.

Separates agent initialization into pre-trust and post-trust phases,
preventing automatic initialization of external resources (MCP connections,
external plugins, tool execution) in untrusted contexts.

Inspired by Claude Code's trust level system.

Usage::

    from sagewai.core.trust import TrustLevel, DeferredInit

    init = DeferredInit(trust_level=TrustLevel.UNTRUSTED)

    # Pre-trust: metadata, config validation only
    assert init.is_pre_trust_complete
    assert not init.is_post_trust_complete

    # Elevate to enable full capabilities
    init.elevate(TrustLevel.TRUSTED)
    result = await init.run_post_trust()
"""

from __future__ import annotations

import enum
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class TrustLevel(enum.IntEnum):
    """Agent trust levels in ascending order."""

    UNTRUSTED = 0
    SANDBOXED = 1
    TRUSTED = 2


@dataclass
class DeferredInitResult:
    """Tracks what was initialized in the post-trust phase."""

    mcp_initialized: bool = False
    plugins_loaded: list[str] = field(default_factory=list)
    tools_enabled: bool = False
    external_services: list[str] = field(default_factory=list)


class DeferredInit:
    """Manages trust-gated initialization phases.

    Pre-trust phase (always runs):
    - Load and validate configuration
    - Register tool specs (metadata only, not execution)
    - Set up event listeners
    - Initialize local state

    Post-trust phase (requires elevation):
    - Connect to MCP servers
    - Load external plugins
    - Enable tool execution against external services
    - Initialize external connections (databases, APIs)

    Parameters
    ----------
    trust_level:
        Initial trust level. Defaults to UNTRUSTED.
    auto_elevate:
        If True, automatically elevate to TRUSTED on first use.
        This is the backward-compatible default.
    """

    def __init__(
        self,
        *,
        trust_level: TrustLevel = TrustLevel.UNTRUSTED,
        auto_elevate: bool = True,
    ) -> None:
        self._trust_level = trust_level
        self._auto_elevate = auto_elevate
        self._pre_trust_complete = False
        self._post_trust_complete = False
        self._init_result: DeferredInitResult | None = None
        self._post_trust_callbacks: list[Any] = []

    @property
    def trust_level(self) -> TrustLevel:
        """Current trust level."""
        return self._trust_level

    @property
    def is_pre_trust_complete(self) -> bool:
        """Whether pre-trust initialization has completed."""
        return self._pre_trust_complete

    @property
    def is_post_trust_complete(self) -> bool:
        """Whether post-trust initialization has completed."""
        return self._post_trust_complete

    @property
    def is_fully_initialized(self) -> bool:
        """Whether both pre-trust and post-trust phases are complete."""
        return self._pre_trust_complete and self._post_trust_complete

    @property
    def init_result(self) -> DeferredInitResult | None:
        """Result from post-trust initialization, or None if not yet run."""
        return self._init_result

    def complete_pre_trust(self) -> None:
        """Mark pre-trust initialization as complete."""
        self._pre_trust_complete = True
        logger.debug(
            "Pre-trust initialization complete (level=%s)",
            self._trust_level.name,
        )

    def elevate(self, level: TrustLevel) -> None:
        """Elevate trust level.

        Can only increase trust, never decrease.

        Raises:
            ValueError: If trying to lower trust level.
        """
        if level < self._trust_level:
            raise ValueError(
                f"Cannot lower trust level from {self._trust_level.name}"
                f" to {level.name}"
            )
        old = self._trust_level
        self._trust_level = level
        logger.info("Trust elevated: %s -> %s", old.name, level.name)

    def requires_trust(self, minimum: TrustLevel) -> bool:
        """Check if current trust level meets the minimum requirement.

        When *auto_elevate* is True and the current level is below
        *minimum*, the level is silently raised.

        Returns:
            True if the (possibly elevated) level meets *minimum*.
        """
        if self._auto_elevate and self._trust_level < minimum:
            self.elevate(minimum)
            return True
        return self._trust_level >= minimum

    def register_post_trust_callback(self, callback: Any) -> None:
        """Register a callback to run during post-trust initialization.

        *callback* may be a sync or async callable.  If it returns a
        string, the string is appended to
        :pyattr:`DeferredInitResult.external_services`.
        """
        self._post_trust_callbacks.append(callback)

    async def run_post_trust(self) -> DeferredInitResult:
        """Execute post-trust initialization callbacks.

        Raises:
            PermissionError: If trust level is UNTRUSTED and
                *auto_elevate* is False.
        """
        if self._trust_level == TrustLevel.UNTRUSTED and not self._auto_elevate:
            raise PermissionError(
                "Cannot run post-trust initialization at UNTRUSTED level. "
                "Call elevate(TrustLevel.SANDBOXED) or "
                "elevate(TrustLevel.TRUSTED) first."
            )

        if self._auto_elevate and self._trust_level < TrustLevel.TRUSTED:
            self.elevate(TrustLevel.TRUSTED)

        result = DeferredInitResult()

        for callback in self._post_trust_callbacks:
            try:
                ret = callback()
                if inspect.isawaitable(ret):
                    ret = await ret
                if isinstance(ret, str):
                    result.external_services.append(ret)
            except Exception:
                logger.exception("Post-trust callback failed")

        result.tools_enabled = self._trust_level >= TrustLevel.SANDBOXED
        self._post_trust_complete = True
        self._init_result = result

        logger.info(
            "Post-trust initialization complete: tools=%s, services=%s",
            result.tools_enabled,
            result.external_services,
        )
        return result

    def guard(
        self,
        operation: str,
        minimum: TrustLevel = TrustLevel.SANDBOXED,
    ) -> None:
        """Guard an operation by trust level.

        If *auto_elevate* is True, silently elevates.  Otherwise raises.

        Args:
            operation: Description of the guarded operation (for error
                messages).
            minimum: Minimum trust level required.

        Raises:
            PermissionError: If trust level is insufficient and
                *auto_elevate* is False.
        """
        if self._trust_level >= minimum:
            return

        if self._auto_elevate:
            self.elevate(minimum)
            return

        raise PermissionError(
            f"Operation '{operation}' requires trust level"
            f" {minimum.name}, current level is"
            f" {self._trust_level.name}."
            f" Call agent.elevate_trust({minimum.name}) first."
        )
