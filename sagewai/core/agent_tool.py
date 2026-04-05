# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Wrap a BaseAgent as a ToolSpec for use by orchestrator agents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sagewai.models.tool import ToolSpec

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent


def agent_as_tool(
    agent: BaseAgent,
    description: str,
    *,
    tool_name: str | None = None,
) -> ToolSpec:
    """Wrap *agent* so another agent can invoke it as a tool.

    The returned ToolSpec has a single ``query`` parameter.  When called,
    the handler runs ``await agent.chat(query)`` and returns the text result.

    Args:
        agent: The sub-agent to wrap.
        description: Human-readable description shown to the LLM.
        tool_name: Override the auto-generated tool name (default: sanitised
            version of ``agent.name``).

    Returns:
        A ToolSpec whose handler delegates to the sub-agent.
    """
    name = tool_name or _sanitize_name(agent.config.name)

    async def _handler(query: str) -> str:
        return await agent.chat(query)

    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The message to send to the sub-agent.",
                },
            },
            "required": ["query"],
        },
        handler=_handler,
    )


def _sanitize_name(name: str) -> str:
    """Convert an agent name to a valid tool name (alphanumeric + underscore)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)
