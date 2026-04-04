# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Reusable test utilities for Sagewai agents.

Provides ``AgentTestHarness`` for setting up deterministic agent tests with
mock LLM responses, plus assertion helpers for tool calls and conversations.

Also provides ``McpContractTest`` for cross-boundary contract testing of
MCP servers against expected tool schemas.

Usage::

    from sagewai.testing import AgentTestHarness

    harness = AgentTestHarness(
        responses=[ChatMessage.assistant("Hello!")],
        tools=[my_tool],
    )
    result = await harness.chat("Hi")
    assert result == "Hello!"
    harness.assert_no_tool_calls()
"""

from sagewai.testing.contract_test import (
    ContractViolationError,
    McpContractTest,
    ServerContract,
    ToolContract,
)
from sagewai.testing.harness import AgentTestHarness, MockAgent

__all__ = [
    "AgentTestHarness",
    "ContractViolationError",
    "McpContractTest",
    "MockAgent",
    "ServerContract",
    "ToolContract",
]
