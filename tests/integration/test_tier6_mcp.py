"""Tier 6: MCP Protocol — real tool connectivity.

Scenarios 29-31:
29. Connect to MCP server via stdio
30. Agent uses MCP tools in loop
31. Multiple MCP servers simultaneously

NOTE: The standalone mcp-servers/ were migrated to sagewai/connectors/builtins
in PR #356.  These tests reference the old mcp_knowledge_graph / mcp_calendar
modules which no longer exist as importable packages.  Tests are skipped until
they are rewritten to use the connector-based servers.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.mcp.client import McpClient

# Check if the old MCP server modules are importable
_has_mcp_kg = importlib.util.find_spec("mcp_knowledge_graph") is not None
_has_mcp_cal = importlib.util.find_spec("mcp_calendar") is not None

# --- Scenario 29: MCP stdio connection ---


@pytest.mark.integration
@pytest.mark.mcp
@pytest.mark.skipif(not _has_mcp_kg, reason="mcp_knowledge_graph migrated to connectors (#356)")
async def test_mcp_stdio_connection():
    """Connect to knowledge-graph MCP server, discover tools."""
    tools = await McpClient.connect(
        [sys.executable, "-m", "mcp_knowledge_graph"],
    )
    assert len(tools) > 0
    tool_names = [t.name for t in tools]
    assert any(
        "entity" in name.lower() or "relation" in name.lower() for name in tool_names
    ), f"Expected entity/relation tools, got: {tool_names}"


# --- Scenario 30: Agent with MCP tools ---


@pytest.mark.integration
@pytest.mark.mcp
@pytest.mark.skipif(not _has_mcp_kg, reason="mcp_knowledge_graph migrated to connectors (#356)")
async def test_agent_with_mcp_tools():
    """Agent uses MCP-provided tools in its agentic loop."""
    tools = await McpClient.connect(
        [sys.executable, "-m", "mcp_knowledge_graph"],
    )
    agent = UniversalAgent(
        name="mcp-agent",
        model="claude-haiku-4-5-20251001",
        tools=tools,
        system_prompt="Use the available tools to manage a knowledge graph.",
    )
    response = await agent.chat("Create an entity called 'Sagewai' with type 'framework'.")
    assert len(response) > 0


# --- Scenario 31: Multiple MCP servers ---


@pytest.mark.integration
@pytest.mark.mcp
@pytest.mark.skipif(
    not (_has_mcp_kg and _has_mcp_cal),
    reason="mcp_knowledge_graph/mcp_calendar migrated to connectors (#356)",
)
async def test_multiple_mcp_servers():
    """Agent connects to multiple MCP servers simultaneously."""
    kg_tools = await McpClient.connect([sys.executable, "-m", "mcp_knowledge_graph"])
    cal_tools = await McpClient.connect([sys.executable, "-m", "mcp_calendar"])

    all_tools = kg_tools + cal_tools
    agent = UniversalAgent(
        name="multi-mcp-agent",
        model="claude-haiku-4-5-20251001",
        tools=all_tools,
        system_prompt="You have access to knowledge graph and calendar tools.",
    )
    # Verify it can handle multiple tool sources
    response = await agent.chat("List the tools you have access to.")
    assert len(response) > 0
