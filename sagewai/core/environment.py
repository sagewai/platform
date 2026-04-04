# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Environment modes — SIMULATION, STAGING, PRODUCTION.

Controls how agents execute tool calls:
- SIMULATION: auto-mock all tool calls, return synthetic data
- STAGING: real tool calls with audit logging
- PRODUCTION: full execution, no extra overhead
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from sagewai.models.tool import ToolResult

logger = logging.getLogger(__name__)

_current_mode: ContextVar[EnvironmentMode] = ContextVar(
    "_current_mode", default=None  # type: ignore[arg-type]
)


class EnvironmentMode(str, Enum):
    """Execution mode for the agent runtime."""

    SIMULATION = "simulation"
    STAGING = "staging"
    PRODUCTION = "production"


class EnvironmentConfig(BaseModel):
    """Configuration for environment mode behavior."""

    mode: EnvironmentMode = EnvironmentMode.PRODUCTION
    simulation_responses: dict[str, str] = Field(
        default_factory=dict,
        description="Tool name → synthetic response for SIMULATION mode",
    )
    audit_log: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Audit trail for STAGING mode",
    )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> EnvironmentConfig:
        self._token = _current_mode.set(self.mode)
        return self

    def __exit__(self, *args: Any) -> None:
        _current_mode.reset(self._token)

    async def __aenter__(self) -> EnvironmentConfig:
        self._token = _current_mode.set(self.mode)
        return self

    async def __aexit__(self, *args: Any) -> None:
        _current_mode.reset(self._token)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def wrap_tool_result(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        real_result: ToolResult | None = None,
    ) -> ToolResult | None:
        """Apply environment mode logic to a tool call.

        Returns:
            A synthetic ToolResult if the mode intercepts the call (SIMULATION),
            or None if the real tool should execute (STAGING, PRODUCTION).
            In STAGING mode, the call is logged to the audit trail.
        """
        if self.mode == EnvironmentMode.SIMULATION:
            synthetic = self.simulation_responses.get(
                tool_name,
                json.dumps({"status": "simulated", "tool": tool_name}),
            )
            logger.info("[SIMULATION] Mock tool call: %s(%s)", tool_name, arguments)
            return ToolResult(
                tool_call_id=tool_call_id,
                name=tool_name,
                content=synthetic,
            )

        if self.mode == EnvironmentMode.STAGING:
            entry = {
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "result": real_result.content if real_result else None,
                "error": real_result.error if real_result else None,
            }
            self.audit_log.append(entry)
            logger.info("[STAGING] Audit: %s(%s)", tool_name, arguments)
            return None  # let real execution proceed

        # PRODUCTION — no interception
        return None

    def should_mock(self) -> bool:
        """Return True if tool calls should be mocked (SIMULATION mode)."""
        return self.mode == EnvironmentMode.SIMULATION

    def clear_audit_log(self) -> None:
        """Clear the audit trail."""
        self.audit_log.clear()


# ------------------------------------------------------------------
# Module-level accessors
# ------------------------------------------------------------------


def get_current_mode() -> EnvironmentMode | None:
    """Return the environment mode for the current context, or None."""
    return _current_mode.get(None)


def set_global_mode(mode: EnvironmentMode) -> None:
    """Set a global default environment mode."""
    _current_mode.set(mode)
