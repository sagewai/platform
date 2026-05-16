# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""MCP contract testing framework.

Validates that MCP server tool definitions match expected contracts,
catching schema drift (renamed fields, missing fields, type changes)
before consuming agents break silently.

Usage::

    from sagewai.testing.contracts import McpContractTest, ServerContract, ToolContract

    contract = ServerContract(
        server_name="knowledge-graph",
        tool_count=8,
        tools={
            "add_entity": ToolContract(
                name="add_entity",
                required_params=["name"],
                optional_params=["entity_type", "properties"],
                param_types={"name": "string", "entity_type": "string", "properties": "object"},
            ),
        },
    )

    tester = McpContractTest(contract, actual_tools_list)
    tester.verify_all()  # raises ContractViolationError with all drift details
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ContractViolationError(AssertionError):
    """Raised when MCP server tools violate their expected contract."""

    def __init__(self, server_name: str, violations: list[str]) -> None:
        self.server_name = server_name
        self.violations = violations
        msg = f"Contract violations for '{server_name}' ({len(violations)} issues):\n"
        msg += "\n".join(f"  - {v}" for v in violations)
        super().__init__(msg)


@dataclass
class ToolContract:
    """Expected contract for a single MCP tool."""

    name: str
    required_params: list[str] = field(default_factory=list)
    optional_params: list[str] = field(default_factory=list)
    param_types: dict[str, str] = field(default_factory=dict)
    response_fields: list[str] = field(default_factory=list)

    @property
    def all_params(self) -> list[str]:
        """All declared parameters (required + optional)."""
        return self.required_params + self.optional_params


@dataclass
class ServerContract:
    """Expected contract for an MCP server."""

    server_name: str
    tool_count: int
    tools: dict[str, ToolContract] = field(default_factory=dict)


class McpContractTest:
    """Contract test runner for MCP servers.

    Validates actual MCP tool definitions against expected contracts.
    Collects all violations before raising, so you see all drift at once.

    Parameters
    ----------
    contract:
        Expected server contract.
    actual_tools:
        The ``TOOLS`` list from the MCP server (list of dicts with
        ``name``, ``description``, ``inputSchema``).
    """

    def __init__(self, contract: ServerContract, actual_tools: list[dict[str, Any]]) -> None:
        self._contract = contract
        self._actual_tools = actual_tools
        self._actual_by_name: dict[str, dict[str, Any]] = {
            t["name"]: t for t in actual_tools
        }
        self._violations: list[str] = []

    @property
    def violations(self) -> list[str]:
        """Accumulated violations from the last verify_all() run."""
        return list(self._violations)

    def verify_tool_count(self) -> None:
        """Check the server exposes the expected number of tools."""
        actual = len(self._actual_tools)
        expected = self._contract.tool_count
        if actual != expected:
            self._violations.append(
                f"Tool count mismatch: expected {expected}, got {actual}"
            )

    def verify_tool_names(self) -> None:
        """Check all expected tools exist and no unexpected tools appear."""
        expected_names = set(self._contract.tools.keys())
        actual_names = set(self._actual_by_name.keys())

        missing = expected_names - actual_names
        extra = actual_names - expected_names

        for name in sorted(missing):
            self._violations.append(f"Missing tool: '{name}'")
        for name in sorted(extra):
            self._violations.append(f"Unexpected tool: '{name}'")

    def verify_input_schemas(self) -> None:
        """Check input schemas match expected parameters and types."""
        for tool_name, expected in self._contract.tools.items():
            actual = self._actual_by_name.get(tool_name)
            if actual is None:
                continue  # already caught by verify_tool_names

            schema = actual.get("inputSchema", {})
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))

            # Check required params
            for param in expected.required_params:
                if param not in properties:
                    self._violations.append(
                        f"Tool '{tool_name}': missing required param '{param}'"
                    )
                elif param not in required:
                    self._violations.append(
                        f"Tool '{tool_name}': param '{param}' should be required"
                    )

            # Check optional params exist
            for param in expected.optional_params:
                if param not in properties:
                    self._violations.append(
                        f"Tool '{tool_name}': missing optional param '{param}'"
                    )

            # Check no unexpected required params
            expected_required = set(expected.required_params)
            unexpected_required = required - expected_required
            for param in sorted(unexpected_required):
                if param in properties:
                    self._violations.append(
                        f"Tool '{tool_name}': unexpected required param '{param}'"
                    )

            # Check parameter types
            for param, expected_type in expected.param_types.items():
                if param not in properties:
                    continue  # already caught above
                actual_type = properties[param].get("type", "")
                if actual_type != expected_type:
                    self._violations.append(
                        f"Tool '{tool_name}': param '{param}' type mismatch: "
                        f"expected '{expected_type}', got '{actual_type}'"
                    )

    def verify_tool_descriptions(self) -> None:
        """Check all tools have non-empty descriptions."""
        for tool in self._actual_tools:
            name = tool.get("name", "<unnamed>")
            desc = tool.get("description", "")
            if not desc:
                self._violations.append(f"Tool '{name}': missing description")

    def verify_response_schema(
        self, tool_name: str, response: dict[str, Any]
    ) -> None:
        """Validate a tool call response against expected fields.

        Parameters
        ----------
        tool_name:
            The tool that produced the response.
        response:
            The parsed JSON response content from the tool call.
        """
        expected = self._contract.tools.get(tool_name)
        if expected is None:
            self._violations.append(f"No contract for tool '{tool_name}'")
            return

        if not expected.response_fields:
            return  # no response schema to validate

        if response is None:
            return  # null responses are valid (e.g. not-found cases)

        if isinstance(response, (list, bool, int, float, str)):
            return  # primitive responses don't have fields to check

        for field_name in expected.response_fields:
            if field_name not in response:
                self._violations.append(
                    f"Tool '{tool_name}' response: missing field '{field_name}'"
                )

    def verify_all(self) -> None:
        """Run all contract checks. Raises ContractViolationError if any fail."""
        self._violations.clear()
        self.verify_tool_count()
        self.verify_tool_names()
        self.verify_input_schemas()
        self.verify_tool_descriptions()

        if self._violations:
            raise ContractViolationError(self._contract.server_name, self._violations)
