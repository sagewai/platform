# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MCP contract tests — verify server tool schemas match SDK expectations.

Each test class imports a server's TOOLS list and validates it against the
canonical contract fixtures. If an MCP server changes a tool's schema, these
tests catch the drift before consuming agents break silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sagewai.testing.contracts import (
    ContractViolationError,
    McpContractTest,
    ServerContract,
    ToolContract,
)

# ---------------------------------------------------------------------------
# Path setup: MCP servers live outside the SDK package
# ---------------------------------------------------------------------------

_MCP_ROOT = Path(__file__).resolve().parents[3] / "mcp-servers"


def _add_server_path(server_dir: str) -> None:
    """Add an MCP server directory to sys.path for import."""
    path = str(_MCP_ROOT / server_dir)
    if path not in sys.path:
        sys.path.insert(0, path)


# ---------------------------------------------------------------------------
# McpContractTest unit tests
# ---------------------------------------------------------------------------


class TestContractFramework:
    """Tests for the McpContractTest class itself."""

    def test_passing_contract(self):
        """No violations when actual tools match the contract."""
        tools = [
            {
                "name": "search",
                "description": "Search stuff",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]
        contract = ServerContract(
            server_name="test",
            tool_count=1,
            tools={
                "search": ToolContract(
                    name="search",
                    required_params=["query"],
                    param_types={"query": "string"},
                ),
            },
        )
        tester = McpContractTest(contract, tools)
        tester.verify_all()  # should not raise

    def test_tool_count_mismatch(self):
        """Detects wrong number of tools."""
        contract = ServerContract(server_name="test", tool_count=3, tools={})
        tester = McpContractTest(contract, [{"name": "a", "description": "a", "inputSchema": {}}])
        with pytest.raises(ContractViolationError, match="Tool count mismatch"):
            tester.verify_all()

    def test_missing_tool(self):
        """Detects a tool that should exist but doesn't."""
        contract = ServerContract(
            server_name="test",
            tool_count=0,
            tools={"search": ToolContract(name="search", required_params=["query"])},
        )
        tester = McpContractTest(contract, [])
        with pytest.raises(ContractViolationError, match="Missing tool: 'search'"):
            tester.verify_all()

    def test_unexpected_tool(self):
        """Detects a tool that exists but isn't in the contract."""
        tools = [{"name": "surprise", "description": "Unexpected", "inputSchema": {}}]
        contract = ServerContract(server_name="test", tool_count=1, tools={})
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError, match="Unexpected tool: 'surprise'"):
            tester.verify_all()

    def test_missing_required_param(self):
        """Detects when a required parameter is missing from inputSchema."""
        tools = [
            {
                "name": "search",
                "description": "Search",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
        ]
        contract = ServerContract(
            server_name="test",
            tool_count=1,
            tools={"search": ToolContract(name="search", required_params=["query"])},
        )
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError, match="missing required param 'query'"):
            tester.verify_all()

    def test_param_not_marked_required(self):
        """Detects when a param exists but isn't in required list."""
        tools = [
            {
                "name": "search",
                "description": "Search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": [],
                },
            },
        ]
        contract = ServerContract(
            server_name="test",
            tool_count=1,
            tools={"search": ToolContract(name="search", required_params=["query"])},
        )
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError, match="should be required"):
            tester.verify_all()

    def test_type_mismatch(self):
        """Detects when a parameter type doesn't match."""
        tools = [
            {
                "name": "search",
                "description": "Search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"count": {"type": "string"}},
                    "required": ["count"],
                },
            },
        ]
        contract = ServerContract(
            server_name="test",
            tool_count=1,
            tools={
                "search": ToolContract(
                    name="search",
                    required_params=["count"],
                    param_types={"count": "integer"},
                ),
            },
        )
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError, match="type mismatch"):
            tester.verify_all()

    def test_missing_description(self):
        """Detects tools without descriptions."""
        tools = [{"name": "search", "description": "", "inputSchema": {}}]
        contract = ServerContract(server_name="test", tool_count=1, tools={})
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError, match="missing description"):
            tester.verify_all()

    def test_verify_response_schema(self):
        """verify_response_schema catches missing fields."""
        contract = ServerContract(
            server_name="test",
            tool_count=0,
            tools={
                "get_item": ToolContract(
                    name="get_item",
                    response_fields=["id", "name", "status"],
                ),
            },
        )
        tester = McpContractTest(contract, [])
        tester.verify_response_schema("get_item", {"id": "1", "name": "test"})
        assert len(tester.violations) == 1
        assert "missing field 'status'" in tester.violations[0]

    def test_verify_response_schema_null(self):
        """Null response is allowed (e.g. not-found cases)."""
        contract = ServerContract(
            server_name="test",
            tool_count=0,
            tools={
                "get_item": ToolContract(
                    name="get_item",
                    response_fields=["id", "name"],
                ),
            },
        )
        tester = McpContractTest(contract, [])
        tester.verify_response_schema("get_item", None)
        assert len(tester.violations) == 0

    def test_multiple_violations_collected(self):
        """All violations are collected, not just the first."""
        tools = [
            {"name": "a", "description": "A", "inputSchema": {}},
            {"name": "b", "description": "", "inputSchema": {}},
        ]
        contract = ServerContract(
            server_name="test",
            tool_count=5,
            tools={"c": ToolContract(name="c")},
        )
        tester = McpContractTest(contract, tools)
        with pytest.raises(ContractViolationError) as exc_info:
            tester.verify_all()
        # Should have: count mismatch, missing 'c', unexpected 'a', unexpected 'b', missing desc on 'b'
        assert len(exc_info.value.violations) >= 4


# ---------------------------------------------------------------------------
# Server contract tests — validate actual MCP servers against fixtures
#
# NOTE: The standalone mcp-servers/ directory was migrated to
# sagewai/connectors/builtins/ in PR #356.  These tests imported the old
# server modules, which no longer exist.  They are kept (but skipped) so
# the contract framework unit tests above remain exercised; the contracts
# should be updated to test the connector-based servers in a follow-up.
# ---------------------------------------------------------------------------

_MCP_SERVERS_MIGRATED = not _MCP_ROOT.exists()

# Import contracts
from sagewai.testing.contracts import (  # noqa: E402
    ADMIN_CONTRACT,
    CALENDAR_CONTRACT,
    COMMERCE_CONTRACT,
    DOCUMENTS_CONTRACT,
    EMAIL_CONTRACT,
    KNOWLEDGE_GRAPH_CONTRACT,
    PAYMENTS_CONTRACT,
    SLACK_CONTRACT,
    TRAVEL_CONTRACT,
)


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestKnowledgeGraphContract:
    def test_contract(self):
        _add_server_path("knowledge-graph")
        from mcp_knowledge_graph.server import TOOLS

        McpContractTest(KNOWLEDGE_GRAPH_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestPaymentsContract:
    def test_contract(self):
        _add_server_path("payments")
        from mcp_payments.server import TOOLS

        McpContractTest(PAYMENTS_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestEmailContract:
    def test_contract(self):
        _add_server_path("email")
        from mcp_email.server import TOOLS

        McpContractTest(EMAIL_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestDocumentsContract:
    def test_contract(self):
        _add_server_path("documents")
        from mcp_documents.server import TOOLS

        McpContractTest(DOCUMENTS_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestAdminContract:
    def test_contract(self):
        _add_server_path("admin")
        from mcp_admin.server import TOOLS

        McpContractTest(ADMIN_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestCalendarContract:
    def test_contract(self):
        _add_server_path("calendar")
        from mcp_calendar.server import TOOLS

        McpContractTest(CALENDAR_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestCommerceContract:
    def test_contract(self):
        _add_server_path("commerce")
        from mcp_commerce.server import TOOLS

        McpContractTest(COMMERCE_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestSlackContract:
    def test_contract(self):
        _add_server_path("slack")
        from mcp_slack.server import TOOLS

        McpContractTest(SLACK_CONTRACT, TOOLS).verify_all()


@pytest.mark.skipif(_MCP_SERVERS_MIGRATED, reason="mcp-servers/ migrated to connectors (#356)")
class TestTravelContract:
    def test_contract(self):
        _add_server_path("travel")
        from mcp_travel.server import TOOLS

        McpContractTest(TRAVEL_CONTRACT, TOOLS).verify_all()
