# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for agent_as_tool — wrapping a BaseAgent as a ToolSpec."""

from __future__ import annotations

import pytest

from sagewai.core.agent_tool import agent_as_tool
from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage
from sagewai.models.tool import ToolSpec


class EchoAgent(BaseAgent):
    """Agent that echoes the input back."""

    async def _invoke_llm(self, messages: list[ChatMessage], tools: list[ToolSpec], *, model_override: str | None = None) -> ChatMessage:
        user_msg = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return ChatMessage.assistant(f"Echo: {user_msg}")


class TestAgentAsTool:
    def test_returns_tool_spec(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo agent")
        assert isinstance(spec, ToolSpec)

    def test_tool_name_matches_agent(self):
        agent = EchoAgent(name="my-echo")
        spec = agent_as_tool(agent, description="Echo agent")
        assert spec.name == "my_echo"

    def test_tool_description(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echoes input back")
        assert spec.description == "Echoes input back"

    def test_tool_has_query_parameter(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo")
        assert "query" in spec.parameters["properties"]
        assert spec.parameters["properties"]["query"]["type"] == "string"
        assert "query" in spec.parameters["required"]

    def test_tool_handler_is_callable(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo")
        assert spec.handler is not None
        assert callable(spec.handler)

    @pytest.mark.asyncio
    async def test_handler_calls_agent_chat(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo")
        result = await spec.handler(query="hello world")
        assert result == "Echo: hello world"

    @pytest.mark.asyncio
    async def test_handler_returns_string(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo")
        result = await spec.handler(query="test")
        assert isinstance(result, str)

    def test_custom_name_override(self):
        agent = EchoAgent(name="echo")
        spec = agent_as_tool(agent, description="Echo", tool_name="custom_echo")
        assert spec.name == "custom_echo"

    def test_name_sanitization(self):
        agent = EchoAgent(name="my-echo agent!")
        spec = agent_as_tool(agent, description="Echo")
        # Tool names must be alphanumeric + underscore
        assert spec.name == "my_echo_agent_"


class TestAgentAsToolIntegration:
    """Test that agent-as-tool works inside another agent's tool loop."""

    @pytest.mark.asyncio
    async def test_orchestrator_calls_sub_agent(self):
        sub_agent = EchoAgent(name="sub")
        sub_tool = agent_as_tool(sub_agent, description="Sub-agent that echoes")

        captured_tool_calls: list[str] = []

        class OrchestratorAgent(BaseAgent):
            async def _invoke_llm(self, messages, tools, *, model_override=None):
                # First call: invoke the sub-agent tool
                if not any(m.role == "tool" for m in messages):
                    from sagewai.models.message import ToolCall

                    return ChatMessage.assistant(
                        tool_calls=[
                            ToolCall(id="tc1", name="sub", arguments={"query": "hello"})
                        ]
                    )
                # Second call: return the tool result as final answer
                tool_msg = next(m for m in messages if m.role == "tool")
                captured_tool_calls.append(tool_msg.content)
                return ChatMessage.assistant(f"Sub said: {tool_msg.content}")

        orchestrator = OrchestratorAgent(name="orch", tools=[sub_tool])
        result = await orchestrator.chat("ask sub to say hello")
        assert "Echo: hello" in result
        assert len(captured_tool_calls) == 1
