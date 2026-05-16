# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""
MCP (Model Context Protocol) integration.

Client: connects to MCP servers and exposes their tools as ToolSpecs.
Server: wraps ToolSpecs and serves them via the MCP protocol.
Agent Server: exposes Sagewai gateway agents as MCP tools.
"""

from sagewai.mcp.client import McpClient, McpConnection, ResilientMcpConnection
from sagewai.mcp.server import McpServer

__all__ = ["McpClient", "McpConnection", "McpServer", "ResilientMcpConnection"]
