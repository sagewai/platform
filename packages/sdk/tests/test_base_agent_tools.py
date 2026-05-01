# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for BaseAgent.add_tools() dynamic tool attachment."""

import pytest

from sagewai.models.tool import ToolSpec


def test_add_tools_extends_registry():
    """BaseAgent.add_tools() should extend the tool registry after construction."""
    from sagewai.engines.universal import UniversalAgent

    tool_a = ToolSpec(
        name="tool_a",
        description="Tool A",
        parameters={"type": "object", "properties": {}},
    )
    tool_b = ToolSpec(
        name="tool_b",
        description="Tool B",
        parameters={"type": "object", "properties": {}},
    )

    agent = UniversalAgent(name="test", model="gpt-4o", tools=[tool_a])
    assert "tool_a" in agent._tool_registry
    assert "tool_b" not in agent._tool_registry

    agent.add_tools([tool_b])
    assert "tool_b" in agent._tool_registry
    assert len(agent.config.tools) == 2
