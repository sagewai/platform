# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the Harnessing Agent, middleware, and directive registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sagewai.harness.agent import HarnessingAgent, register_harness_directives
from sagewai.harness.classifier import ComplexityTier, RequestClassifier
from sagewai.harness.middleware import HarnessMiddleware, harness_wrap
from sagewai.harness.models import ModelTierConfig


# ── Fake agent for testing (avoids real LLM calls) ──────────────────


@dataclass
class FakeConfig:
    name: str = "test-agent"
    model: str = "claude-opus-4-6"


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    model: str = ""
    duration_ms: float = 0.0


@dataclass
class FakeMessage:
    role: str = "assistant"
    content: str = "Hello!"
    tool_calls: list[Any] = field(default_factory=list)
    usage: FakeUsage | None = None


class FakeAgent:
    """Minimal agent mock that tracks _call_llm invocations."""

    def __init__(self, name: str = "fake", model: str = "claude-opus-4-6") -> None:
        self.config = FakeConfig(name=name, model=model)
        self._current_model_override: str | None = None
        self._rate_limiter = None
        self._call_count = 0
        self._last_model_override: str | None = None

    async def _call_llm(self, messages: Any, tools: Any) -> FakeMessage:
        """Track calls and return a fake response."""
        self._call_count += 1
        self._last_model_override = self._current_model_override
        return FakeMessage(usage=FakeUsage())

    async def chat(self, message: str) -> str:
        from sagewai.models.message import ChatMessage

        msgs = [ChatMessage.user(message)]
        result = await self._call_llm(msgs, [])
        return result.content


class HarnessedFakeAgent(HarnessMiddleware, FakeAgent):
    """FakeAgent with HarnessMiddleware mixin."""

    pass


# ── Tests ────────────────────────────────────────────────────────────


class TestHarnessMiddleware:
    """Test the HarnessMiddleware mixin."""

    @pytest.mark.asyncio
    async def test_middleware_disabled_by_default(self) -> None:
        """Without enable_harness(), middleware does nothing."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        await agent._call_llm(
            [FakeMessage(role="user", content="hi")], []
        )
        assert agent._call_count == 1
        assert agent._last_model_override is None

    @pytest.mark.asyncio
    async def test_middleware_routes_simple_to_haiku(self) -> None:
        """Simple queries should be routed to the simple tier model."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        agent.enable_harness(
            tier_config=ModelTierConfig(
                simple="haiku", medium="sonnet", complex="opus"
            )
        )

        # Short simple message
        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        assert agent._last_model_override == "haiku"

    @pytest.mark.asyncio
    async def test_middleware_preserves_directive_override(self) -> None:
        """If a directive override is already set, middleware defers."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        agent.enable_harness(
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus")
        )

        # Simulate directive setting model override
        agent._current_model_override = "gpt-4o"
        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        # Should NOT override — directive takes precedence
        assert agent._last_model_override == "gpt-4o"

    @pytest.mark.asyncio
    async def test_middleware_cleans_up_override(self) -> None:
        """Model override should be cleaned up after the call."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        agent.enable_harness(
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus")
        )

        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        # Override should be cleaned up after call
        assert agent._current_model_override is None

    @pytest.mark.asyncio
    async def test_middleware_logs_decisions(self) -> None:
        """Routing decisions should be logged."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        agent.enable_harness(
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus")
        )

        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        log = agent.harness_routing_log
        assert len(log) == 1
        assert log[0]["tier"] == "simple"
        assert log[0]["overridden"] is True
        assert log[0]["target_model"] == "haiku"

    @pytest.mark.asyncio
    async def test_disable_harness(self) -> None:
        """Disabling harness should stop routing."""
        agent = HarnessedFakeAgent(name="test", model="claude-opus-4-6")
        agent.enable_harness(
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus")
        )
        agent.disable_harness()

        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        assert agent._last_model_override is None


class TestHarnessWrap:
    """Test the monkey-patch wrapper."""

    @pytest.mark.asyncio
    async def test_wrap_routes_simple(self) -> None:
        agent = FakeAgent(name="wrapped", model="claude-opus-4-6")
        harness_wrap(
            agent,
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus"),
        )

        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        assert agent._last_model_override == "haiku"

    @pytest.mark.asyncio
    async def test_wrap_logs_decisions(self) -> None:
        agent = FakeAgent(name="wrapped", model="claude-opus-4-6")
        harness_wrap(agent)

        await agent._call_llm(
            [FakeMessage(role="user", content="fix typo")], []
        )
        assert len(agent._harness_log) == 1

    @pytest.mark.asyncio
    async def test_wrap_preserves_original_for_complex(self) -> None:
        """Complex requests should keep the original expensive model."""
        agent = FakeAgent(name="wrapped", model="claude-opus-4-6")
        harness_wrap(
            agent,
            tier_config=ModelTierConfig(
                simple="haiku",
                medium="sonnet",
                complex="claude-opus-4-6",  # same as agent's model
            ),
        )

        # Complex request (many tools, architecture keywords)
        messages = [
            FakeMessage(role="system", content="You are an expert architect. " * 100),
            FakeMessage(
                role="user",
                content=(
                    "Design and implement the complete authentication system "
                    "with security audit and end-to-end review."
                ),
            ),
        ]
        tools = [FakeMessage(role="tool", content=f"tool_{i}") for i in range(8)]
        await agent._call_llm(messages, tools)
        # Should NOT override — complex tier maps to same model
        assert agent._last_model_override is None


class TestHarnessingAgent:
    """Test the supervisory HarnessingAgent."""

    def test_add_agents(self) -> None:
        a1 = FakeAgent(name="agent-1")
        a2 = FakeAgent(name="agent-2")
        harness = HarnessingAgent(
            name="supervisor",
            agents=[a1, a2],
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus"),
        )
        assert len(harness.agents) == 2
        assert a1._harness_enabled
        assert a2._harness_enabled

    def test_remove_agent(self) -> None:
        a1 = FakeAgent(name="agent-1")
        harness = HarnessingAgent(name="supervisor", agents=[a1])
        assert harness.remove_agent("agent-1")
        assert not a1._harness_enabled
        assert len(harness.agents) == 0

    @pytest.mark.asyncio
    async def test_routing_log_across_agents(self) -> None:
        a1 = FakeAgent(name="planner", model="claude-opus-4-6")
        a2 = FakeAgent(name="coder", model="claude-opus-4-6")
        harness = HarnessingAgent(
            name="supervisor",
            agents=[a1, a2],
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus"),
        )

        await a1._call_llm([FakeMessage(role="user", content="fix typo")], [])
        await a2._call_llm([FakeMessage(role="user", content="rename variable")], [])

        log = harness.get_routing_log()
        assert len(log) == 2
        agents_in_log = {entry["agent"] for entry in log}
        assert "planner" in agents_in_log
        assert "coder" in agents_in_log

    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        a1 = FakeAgent(name="agent-1", model="claude-opus-4-6")
        harness = HarnessingAgent(
            name="supervisor",
            agents=[a1],
            tier_config=ModelTierConfig(simple="haiku", medium="sonnet", complex="opus"),
        )

        await a1._call_llm([FakeMessage(role="user", content="fix typo")], [])
        await a1._call_llm([FakeMessage(role="user", content="add import")], [])

        stats = harness.get_stats()
        assert stats["total_calls"] == 2
        assert stats["agent_count"] == 1
        assert stats["total_overrides"] >= 0

    def test_update_tier_config(self) -> None:
        a1 = FakeAgent(name="agent-1")
        harness = HarnessingAgent(name="supervisor", agents=[a1])
        new_config = ModelTierConfig(simple="gpt-4o-mini", medium="gpt-4o", complex="o3")
        harness.update_tier_config(new_config)
        assert a1._harness_tier_config == new_config

    def test_set_enabled(self) -> None:
        a1 = FakeAgent(name="agent-1")
        harness = HarnessingAgent(name="supervisor", agents=[a1])
        harness.set_enabled(False)
        assert not a1._harness_enabled
        harness.set_enabled(True)
        assert a1._harness_enabled


class TestDirectiveRegistration:
    """Test harness directive registration."""

    def test_register_directives(self) -> None:
        """Directives should register without error."""

        class FakeRegistry:
            def __init__(self) -> None:
                self.registered: list[tuple[str, str]] = []

            def register(self, name: str, sigil: str, handler: Any, description: str = "") -> None:
                self.registered.append((name, sigil))

        class FakeEngine:
            def __init__(self) -> None:
                self._registry = FakeRegistry()
                self._resolver = type("R", (), {"_custom_handlers": {}})()

            def register(self, name: str, sigil: str, handler: Any, description: str = "") -> None:
                self._registry.register(name, sigil, handler, description)
                self._resolver._custom_handlers[name] = handler

        engine = FakeEngine()
        register_harness_directives(engine)
        registered_names = [name for name, _ in engine._registry.registered]
        assert "route" in registered_names
        assert "cost" in registered_names

    @pytest.mark.asyncio
    async def test_route_handler(self) -> None:
        """@route directive handler should resolve tier names."""
        handlers: dict[str, Any] = {}

        class FakeEngine:
            def __init__(self) -> None:
                self._registry = type("R", (), {
                    "register": lambda self, n, s, h, d="": handlers.update({n: h})
                })()
                self._resolver = type("R", (), {"_custom_handlers": {}})()

            def register(self, name: str, sigil: str, handler: Any, description: str = "") -> None:
                self._registry.register(name, sigil, handler, description)
                self._resolver._custom_handlers[name] = handler

        engine = FakeEngine()
        register_harness_directives(engine)

        result = await handlers["route"]("simple")
        assert "haiku" in result.lower() or "simple" in result.lower()

        result = await handlers["route"]("complex")
        assert "opus" in result.lower() or "complex" in result.lower()

    @pytest.mark.asyncio
    async def test_cost_handler(self) -> None:
        """@cost directive handler should return pricing info."""
        handlers: dict[str, Any] = {}

        class FakeEngine:
            def __init__(self) -> None:
                self._registry = type("R", (), {
                    "register": lambda self, n, s, h, d="": handlers.update({n: h})
                })()
                self._resolver = type("R", (), {"_custom_handlers": {}})()

            def register(self, name: str, sigil: str, handler: Any, description: str = "") -> None:
                self._registry.register(name, sigil, handler, description)
                self._resolver._custom_handlers[name] = handler

        engine = FakeEngine()
        register_harness_directives(engine)

        result = await handlers["cost"]("claude-opus-4-6")
        assert "15.00" in result  # Opus input pricing
