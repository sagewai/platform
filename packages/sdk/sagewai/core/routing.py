# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""RoutingStrategy — classify user intent and dispatch to a specialist agent."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from sagewai.core.events import AgentEvent
from sagewai.models.message import ChatMessage

if TYPE_CHECKING:
    from sagewai.core.base import BaseAgent
    from sagewai.models.tool import ToolSpec


class RoutingStrategy:
    """Execution strategy that routes to a specialist agent based on intent.

    Two routing methods:
      - ``"heuristic"``: keyword matching (cheap, deterministic).
      - ``"llm"``: asks the host agent's LLM to classify the intent.

    If no route matches, the *fallback* agent handles the request.
    """

    def __init__(
        self,
        *,
        routes: dict[str, BaseAgent],
        fallback: BaseAgent,
        method: Literal["llm", "heuristic"] = "llm",
        keywords: dict[str, list[str]] | None = None,
    ) -> None:
        if not routes:
            raise ValueError("RoutingStrategy requires at least one route.")
        self.routes = routes
        self.fallback = fallback
        self.method = method
        self.keywords = {k: [w.lower() for w in v] for k, v in (keywords or {}).items()}

    async def execute(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_iterations: int,
    ) -> ChatMessage:
        """Classify intent and dispatch to the selected agent."""
        user_message = self._extract_user_message(messages)

        if self.method == "heuristic":
            route_key = self._heuristic_route(user_message)
        else:
            route_key = await self._llm_route(agent, messages)

        selected = self.routes.get(route_key, self.fallback)

        await agent._emit(AgentEvent.ROUTE_SELECTED, {
            "route": route_key if route_key in self.routes else "__fallback__",
            "agent": selected.config.name,
            "method": self.method,
        })

        return await selected.chat_with_history(messages)

    def _extract_user_message(self, messages: list[ChatMessage]) -> str:
        """Get the last user message text."""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                return msg.content
        return ""

    def _heuristic_route(self, message: str) -> str:
        """Match keywords against the user message."""
        lower = message.lower()
        for route_key, kws in self.keywords.items():
            if any(kw in lower for kw in kws):
                return route_key
        return "__none__"

    async def _llm_route(
        self,
        agent: BaseAgent,
        messages: list[ChatMessage],
    ) -> str:
        """Ask the host agent's LLM to select a route."""
        route_descriptions = "\n".join(
            f"- {key}: {a.config.system_prompt or a.config.name}"
            for key, a in self.routes.items()
        )
        routing_prompt = ChatMessage.system(
            f"You are a router. Given the user's message, respond with ONLY the "
            f"route key that best matches. Available routes:\n{route_descriptions}\n\n"
            f"Respond with just the route key, nothing else."
        )

        routing_messages = [routing_prompt] + [
            m for m in messages if m.role == "user"
        ]

        response = await agent._call_llm(routing_messages, [])
        return self._match_route(response.content or "", self.routes)

    @staticmethod
    def _match_route(route_text: str, routes) -> str:
        """Match a route key in the LLM response, tolerant of SLM prose."""
        text = route_text.strip().lower()
        # 1. Exact / prefix match — frontier-model fast path.
        for key in routes:
            if text == key.lower() or text.startswith(key.lower()):
                return key
        # 2. Whole-word containment — SLM prose fallback.
        for key in routes:
            if re.search(rf"\b{re.escape(key.lower())}\b", text):
                return key
        return "__none__"
