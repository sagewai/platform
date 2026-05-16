# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for RoutingStrategy — intent classification and agent dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.routing import RoutingStrategy
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class FixedAgent(BaseAgent):
    """Agent that returns a fixed response."""

    def __init__(self, response: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._response = response

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        return ChatMessage.assistant(self._response)


class RouterLLMAgent(BaseAgent):
    """Agent whose LLM picks a route key from the system prompt."""

    def __init__(self, route_key: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._route_key = route_key

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        return ChatMessage.assistant(self._route_key)


# ---------------------------------------------------------------------------
# RoutingStrategy construction
# ---------------------------------------------------------------------------


class TestRoutingStrategyInit:
    def test_requires_routes(self):
        fallback = FixedAgent(response="fallback", name="fallback")
        strategy = RoutingStrategy(
            routes={"greet": FixedAgent(response="hi", name="greeter")},
            fallback=fallback,
        )
        assert strategy is not None

    def test_empty_routes_raises(self):
        fallback = FixedAgent(response="fallback", name="fallback")
        with pytest.raises(ValueError, match="at least one route"):
            RoutingStrategy(routes={}, fallback=fallback)


# ---------------------------------------------------------------------------
# Heuristic routing
# ---------------------------------------------------------------------------


class TestHeuristicRouting:
    @pytest.mark.asyncio
    async def test_keyword_match(self):
        greeter = FixedAgent(response="Hello!", name="greeter")
        researcher = FixedAgent(response="Found results", name="researcher")
        fallback = FixedAgent(response="I don't know", name="fallback")

        strategy = RoutingStrategy(
            routes={
                "greet": greeter,
                "research": researcher,
            },
            fallback=fallback,
            method="heuristic",
            keywords={"greet": ["hello", "hi", "hey"], "research": ["find", "search", "look up"]},
        )

        # Use a dummy agent as the host — strategy overrides it
        host = FixedAgent(response="unused", name="host")
        host._strategy = strategy

        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("hello there")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Hello!"

    @pytest.mark.asyncio
    async def test_keyword_no_match_falls_back(self):
        greeter = FixedAgent(response="Hello!", name="greeter")
        fallback = FixedAgent(response="Fallback response", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter},
            fallback=fallback,
            method="heuristic",
            keywords={"greet": ["hello", "hi"]},
        )

        host = FixedAgent(response="unused", name="host")
        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("what is quantum physics")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Fallback response"

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self):
        greeter = FixedAgent(response="Hello!", name="greeter")
        fallback = FixedAgent(response="Fallback", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter},
            fallback=fallback,
            method="heuristic",
            keywords={"greet": ["hello"]},
        )

        host = FixedAgent(response="unused", name="host")
        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("HELLO WORLD")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Hello!"


# ---------------------------------------------------------------------------
# LLM-based routing
# ---------------------------------------------------------------------------


class TestLLMRouting:
    @pytest.mark.asyncio
    async def test_llm_selects_route(self):
        greeter = FixedAgent(response="Hello from greeter!", name="greeter")
        researcher = FixedAgent(response="Research results", name="researcher")
        fallback = FixedAgent(response="Fallback", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter, "research": researcher},
            fallback=fallback,
            method="llm",
        )

        # The host agent's LLM returns "greet" as the route key
        host = RouterLLMAgent(route_key="greet", name="router")
        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("say hi")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Hello from greeter!"

    @pytest.mark.asyncio
    async def test_llm_unknown_route_falls_back(self):
        greeter = FixedAgent(response="Hello!", name="greeter")
        fallback = FixedAgent(response="Fallback", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter},
            fallback=fallback,
            method="llm",
        )

        host = RouterLLMAgent(route_key="unknown_route", name="router")
        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("something weird")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Fallback"

    @pytest.mark.asyncio
    async def test_llm_route_with_extra_text(self):
        """LLM may respond with 'greet - the user wants a greeting'. Should still match."""
        greeter = FixedAgent(response="Hello!", name="greeter")
        fallback = FixedAgent(response="Fallback", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter},
            fallback=fallback,
            method="llm",
        )

        host = RouterLLMAgent(route_key="greet - user wants greeting", name="router")
        result = await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("hi")],
            tools=[],
            max_iterations=1,
        )
        assert result.content == "Hello!"


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestRoutingEvents:
    @pytest.mark.asyncio
    async def test_emits_route_selected_event(self):
        greeter = FixedAgent(response="Hello!", name="greeter")
        fallback = FixedAgent(response="Fallback", name="fallback")

        strategy = RoutingStrategy(
            routes={"greet": greeter},
            fallback=fallback,
            method="heuristic",
            keywords={"greet": ["hello"]},
        )

        events: list[tuple] = []
        host = FixedAgent(response="unused", name="host")
        host.on_event(lambda event, data: events.append((event, data)))

        await strategy.execute(
            agent=host,
            messages=[ChatMessage.user("hello")],
            tools=[],
            max_iterations=1,
        )

        from sagewai.core.events import AgentEvent

        route_events = [(e, d) for e, d in events if e == AgentEvent.ROUTE_SELECTED]
        assert len(route_events) == 1
        assert route_events[0][1]["route"] == "greet"


# ---------------------------------------------------------------------------
# _match_route static helper — SLM prose tolerance
# ---------------------------------------------------------------------------


def test_route_match_tolerates_slm_prose():
    """An SLM that wraps the route key in prose still routes correctly."""
    routes = {"billing": "Billing questions", "support": "Technical support"}
    assert RoutingStrategy._match_route("billing", routes) == "billing"
    assert RoutingStrategy._match_route("route: billing", routes) == "billing"
    assert RoutingStrategy._match_route(
        "I think this is a billing question.", routes
    ) == "billing"
    assert RoutingStrategy._match_route("totally unrelated", routes) == "__none__"
