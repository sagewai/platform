# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for in-process agent registry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.core.registry import AgentRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_agent(name: str, tools: list[str] | None = None) -> MagicMock:
    """Create a mock BaseAgent with given name and tool names."""
    agent = MagicMock()
    agent.config.name = name
    if tools:
        agent.config.tools = [MagicMock(name=t) for t in tools]
    else:
        agent.config.tools = []
    agent.chat = AsyncMock(return_value=f"Response from {name}")
    return agent


@pytest.fixture
def registry():
    """Fresh registry for each test."""
    return AgentRegistry()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_with_capabilities(self, registry):
        """register() stores agent with explicit capabilities."""
        agent = _mock_agent("scout")
        registry.register(agent, capabilities=["research", "search"])
        assert "scout" in registry
        assert len(registry) == 1

    def test_register_auto_capabilities_from_tools(self, registry):
        """register() extracts capabilities from tool names when not provided."""
        agent = _mock_agent("writer", tools=["draft_post", "edit_text"])
        registry.register(agent)
        agents_info = registry.list_agents()
        # MagicMock(name="draft_post").name is the mock's name attribute
        assert "writer" in agents_info

    def test_register_overwrites_existing(self, registry):
        """Registering same agent name overwrites previous entry."""
        agent1 = _mock_agent("agent")
        agent2 = _mock_agent("agent")
        registry.register(agent1, capabilities=["a"])
        registry.register(agent2, capabilities=["b"])
        assert len(registry) == 1
        assert registry.list_agents()["agent"] == ["b"]

    def test_unregister(self, registry):
        """unregister() removes agent by name."""
        agent = _mock_agent("scout")
        registry.register(agent, capabilities=["research"])
        registry.unregister("scout")
        assert "scout" not in registry
        assert len(registry) == 0

    def test_unregister_nonexistent(self, registry):
        """unregister() is a no-op for unknown agents."""
        registry.unregister("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_discover_single_match(self, registry):
        """discover() returns agents with matching capability."""
        scout = _mock_agent("scout")
        writer = _mock_agent("writer")
        registry.register(scout, capabilities=["research", "search"])
        registry.register(writer, capabilities=["writing"])

        result = registry.discover("research")
        assert len(result) == 1
        assert result[0].config.name == "scout"

    def test_discover_multiple_matches(self, registry):
        """discover() returns all agents with the capability."""
        agent1 = _mock_agent("agent1")
        agent2 = _mock_agent("agent2")
        registry.register(agent1, capabilities=["writing"])
        registry.register(agent2, capabilities=["writing", "editing"])

        result = registry.discover("writing")
        assert len(result) == 2

    def test_discover_no_match(self, registry):
        """discover() returns empty list when no match."""
        agent = _mock_agent("scout")
        registry.register(agent, capabilities=["research"])

        result = registry.discover("cooking")
        assert result == []

    def test_discover_empty_registry(self, registry):
        """discover() returns empty list on empty registry."""
        assert registry.discover("anything") == []


# ---------------------------------------------------------------------------
# Get tests
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_existing(self, registry):
        """get() returns agent by name."""
        agent = _mock_agent("scout")
        registry.register(agent, capabilities=["research"])
        assert registry.get("scout") is agent

    def test_get_nonexistent(self, registry):
        """get() returns None for unknown name."""
        assert registry.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


class TestDelegate:
    @pytest.mark.asyncio
    async def test_delegate_to_first_match(self, registry):
        """delegate() sends task to first matching agent."""
        scout = _mock_agent("scout")
        registry.register(scout, capabilities=["research"])

        result = await registry.delegate("research", "Find AI trends")
        assert result == "Response from scout"
        scout.chat.assert_called_once_with("Find AI trends")

    @pytest.mark.asyncio
    async def test_delegate_to_named_agent(self, registry):
        """delegate() sends task to specific named agent."""
        scout = _mock_agent("scout")
        writer = _mock_agent("writer")
        registry.register(scout, capabilities=["research"])
        registry.register(writer, capabilities=["research", "writing"])

        result = await registry.delegate("research", "Find info", agent_name="writer")
        assert result == "Response from writer"

    @pytest.mark.asyncio
    async def test_delegate_no_match_raises(self, registry):
        """delegate() raises ValueError when no agent matches."""
        with pytest.raises(ValueError, match="No agent found"):
            await registry.delegate("cooking", "Make pasta")

    @pytest.mark.asyncio
    async def test_delegate_named_agent_wrong_capability(self, registry):
        """delegate() raises when named agent lacks the capability."""
        agent = _mock_agent("scout")
        registry.register(agent, capabilities=["research"])

        with pytest.raises(ValueError, match="lacks capability"):
            await registry.delegate("writing", "Write post", agent_name="scout")

    @pytest.mark.asyncio
    async def test_delegate_named_agent_not_found(self, registry):
        """delegate() raises when named agent doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await registry.delegate("research", "Find info", agent_name="ghost")


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_instance_returns_same(self):
        """get_instance() returns the same registry."""
        AgentRegistry.reset_instance()
        r1 = AgentRegistry.get_instance()
        r2 = AgentRegistry.get_instance()
        assert r1 is r2
        AgentRegistry.reset_instance()

    def test_reset_instance(self):
        """reset_instance() clears the singleton."""
        AgentRegistry.reset_instance()
        r1 = AgentRegistry.get_instance()
        AgentRegistry.reset_instance()
        r2 = AgentRegistry.get_instance()
        assert r1 is not r2
        AgentRegistry.reset_instance()


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------


class TestUtilities:
    def test_clear(self, registry):
        """clear() removes all agents."""
        registry.register(_mock_agent("a"), capabilities=["x"])
        registry.register(_mock_agent("b"), capabilities=["y"])
        assert len(registry) == 2
        registry.clear()
        assert len(registry) == 0

    def test_list_agents(self, registry):
        """list_agents() returns name→capabilities mapping."""
        registry.register(_mock_agent("scout"), capabilities=["research", "search"])
        registry.register(_mock_agent("writer"), capabilities=["writing"])
        info = registry.list_agents()
        assert info == {"scout": ["research", "search"], "writer": ["writing"]}

    def test_contains(self, registry):
        """__contains__ works with 'in' operator."""
        registry.register(_mock_agent("scout"), capabilities=["research"])
        assert "scout" in registry
        assert "writer" not in registry

    def test_len(self, registry):
        """__len__ returns agent count."""
        assert len(registry) == 0
        registry.register(_mock_agent("a"), capabilities=["x"])
        assert len(registry) == 1
