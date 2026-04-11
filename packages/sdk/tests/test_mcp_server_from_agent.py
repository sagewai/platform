"""Tests for McpServer.from_agent() classmethod."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from sagewai.mcp.server import McpServer
from sagewai.models.tool import ToolSpec


def test_from_agent_creates_server():
    """McpServer.from_agent() should create a server with agent tools + chat tool."""
    mock_agent = MagicMock()
    mock_agent.config.name = "my-agent"
    mock_agent.config.tools = [
        ToolSpec(
            name="search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        ),
    ]

    server = McpServer.from_agent(mock_agent)

    assert isinstance(server, McpServer)
    tool_names = [t.name for t in server.tools]
    assert "chat" in tool_names  # always-present chat tool
    assert "search" in tool_names  # mirrored from agent


@pytest.mark.asyncio
async def test_from_agent_chat_tool_calls_agent():
    """The chat tool should call agent.chat() and return the response."""
    mock_agent = MagicMock()
    mock_agent.config.name = "my-agent"
    mock_agent.config.tools = []
    mock_agent.chat = AsyncMock(return_value="Hello from agent!")

    server = McpServer.from_agent(mock_agent)
    chat_tool = next(t for t in server.tools if t.name == "chat")

    result = await chat_tool.handler(message="Hi")
    assert result == "Hello from agent!"
    mock_agent.chat.assert_called_once_with("Hi")
