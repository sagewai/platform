# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Agent test harness for deterministic integration testing.

``AgentTestHarness`` wraps a ``MockAgent`` that replays predetermined LLM
responses, making it easy to write end-to-end tests for tool-calling flows
without hitting real LLM APIs.
"""

from __future__ import annotations

from typing import Any

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage, Role, ToolCall
from sagewai.models.tool import ToolSpec


class MockAgent(BaseAgent):
    """Agent that returns predetermined responses in order.

    Each call to ``_call_llm`` pops the next response from the queue.
    Raises ``IndexError`` if more calls are made than responses provided.
    """

    def __init__(self, responses: list[ChatMessage], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._responses = list(responses)
        self._call_count = 0
        self._call_log: list[list[ChatMessage]] = []

    async def _invoke_llm(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        model_override: str | None = None,
    ) -> ChatMessage:
        self._call_log.append(list(messages))
        if self._call_count >= len(self._responses):
            raise IndexError(
                f"MockAgent exhausted all {len(self._responses)} responses "
                f"but _invoke_llm was called again (call #{self._call_count + 1})"
            )
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


class AgentTestHarness:
    """High-level test harness for agent integration tests.

    Wraps a :class:`MockAgent` with convenience methods for running
    conversations and asserting on the results.

    Args:
        responses: Ordered list of ChatMessage objects the mock LLM will return.
        tools: Optional list of ToolSpec objects to register with the agent.
        name: Agent name (default: "test-agent").
        model: Model identifier (default: "mock").
        system_prompt: Optional system prompt.
        max_iterations: Max tool-calling loop iterations (default: 10).

    Usage::

        harness = AgentTestHarness(
            responses=[
                ChatMessage.assistant(
                    tool_calls=[ToolCall(id="1", name="search", arguments={"q": "test"})]
                ),
                ChatMessage.assistant("Found it!"),
            ],
            tools=[search_tool],
        )
        result = await harness.chat("Search for test")
        assert result == "Found it!"
        harness.assert_tool_called("search", times=1)
        harness.assert_tool_called_with("search", q="test")
    """

    def __init__(
        self,
        responses: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        name: str = "test-agent",
        model: str = "mock",
        system_prompt: str = "",
        max_iterations: int = 10,
    ) -> None:
        self.agent = MockAgent(
            responses=responses,
            name=name,
            model=model,
            system_prompt=system_prompt,
            tools=tools or [],
            max_iterations=max_iterations,
        )
        self._tool_executions: list[ToolCall] = []
        self._results: list[str] = []

    async def chat(self, message: str) -> str:
        """Send a message and return the text response."""
        result = await self.agent.chat(message)
        self._results.append(result)
        self._collect_tool_calls()
        return result

    async def chat_with_history(self, messages: list[ChatMessage]) -> ChatMessage:
        """Run with explicit history and return the final message."""
        result = await self.agent.chat_with_history(messages)
        self._collect_tool_calls()
        return result

    def _collect_tool_calls(self) -> None:
        """Extract tool calls from the agent's call log."""
        self._tool_executions.clear()
        for messages in self.agent._call_log:
            for msg in messages:
                if msg.role == Role.assistant and msg.tool_calls:
                    self._tool_executions.extend(msg.tool_calls)

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    @property
    def call_count(self) -> int:
        """Number of times _call_llm was invoked."""
        return self.agent._call_count

    @property
    def tool_calls(self) -> list[ToolCall]:
        """All tool calls made during the conversation."""
        all_calls: list[ToolCall] = []
        for response in self.agent._responses[: self.agent._call_count]:
            if response.tool_calls:
                all_calls.extend(response.tool_calls)
        return all_calls

    @property
    def messages_sent(self) -> list[list[ChatMessage]]:
        """All message lists sent to _call_llm, one per invocation."""
        return self.agent._call_log

    def assert_no_tool_calls(self) -> None:
        """Assert that no tool calls were made."""
        calls = self.tool_calls
        assert not calls, f"Expected no tool calls, but got {len(calls)}: {calls}"

    def assert_tool_called(self, name: str, *, times: int | None = None) -> None:
        """Assert a tool was called, optionally a specific number of times."""
        calls = [tc for tc in self.tool_calls if tc.name == name]
        assert calls, (
            f"Expected tool '{name}' to be called, but it wasn't. "
            f"Tools called: {[tc.name for tc in self.tool_calls]}"
        )
        if times is not None:
            assert len(calls) == times, (
                f"Expected tool '{name}' to be called {times} time(s), "
                f"but it was called {len(calls)} time(s)"
            )

    def assert_tool_called_with(self, name: str, **expected_args: Any) -> None:
        """Assert a tool was called with specific arguments."""
        calls = [tc for tc in self.tool_calls if tc.name == name]
        assert calls, f"Tool '{name}' was never called"
        for tc in calls:
            if all(tc.arguments.get(k) == v for k, v in expected_args.items()):
                return
        raise AssertionError(
            f"Tool '{name}' was called but never with args {expected_args}. "
            f"Actual calls: {[(tc.name, tc.arguments) for tc in calls]}"
        )

    def assert_call_count(self, expected: int) -> None:
        """Assert the number of LLM calls made."""
        assert self.call_count == expected, f"Expected {expected} LLM calls, got {self.call_count}"

    def assert_final_response_contains(self, text: str) -> None:
        """Assert the last result contains a substring."""
        assert self._results, "No chat results recorded"
        last = self._results[-1]
        assert text in last, f"Expected final response to contain '{text}', got: '{last}'"
