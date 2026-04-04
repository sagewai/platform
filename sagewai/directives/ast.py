# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AST node types for the Sagewai Directive Engine.

The tokenizer produces tokens; the parser builds them into an AST of these nodes.
The resolver walks the AST and resolves each node concurrently.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DirectiveType(str, Enum):
    """Directive categories."""

    CONTEXT = "context"
    MEMORY = "memory"
    AGENT = "agent"
    WORKFLOW = "workflow"
    TOOL = "tool"
    MCP = "mcp"
    META = "meta"
    TEXT = "text"


class MetaKey(str, Enum):
    """Recognized meta-directive keys (# directives)."""

    MODEL = "model"
    SCOPE = "scope"
    STRATEGY = "strategy"
    BUDGET = "budget"


# ---------------------------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------------------------


class ASTNode(BaseModel):
    """Base class for all AST nodes."""

    node_type: DirectiveType


class TextNode(ASTNode):
    """Plain text that passes through without resolution."""

    node_type: DirectiveType = DirectiveType.TEXT
    text: str


class ContextNode(ASTNode):
    """@context('query') — semantic search via Context Engine."""

    node_type: DirectiveType = DirectiveType.CONTEXT
    query: str
    scope: str | None = None
    top_k: int = 5
    tags: list[str] | None = None


class MemoryNode(ASTNode):
    """@memory('key') — retrieve from memory provider."""

    node_type: DirectiveType = DirectiveType.MEMORY
    query: str
    scope: str | None = None


class AgentNode(ASTNode):
    """@agent:name('question') — delegate to another agent via A2A."""

    node_type: DirectiveType = DirectiveType.AGENT
    agent_name: str
    question: str


class WorkflowNode(ASTNode):
    """@wf:name('input') — invoke a saved workflow."""

    node_type: DirectiveType = DirectiveType.WORKFLOW
    workflow_name: str
    input_text: str


class ToolNode(ASTNode):
    """/tool.name('args') — invoke a registered tool."""

    node_type: DirectiveType = DirectiveType.TOOL
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arg: str | None = None


class McpNode(ASTNode):
    """/mcp.connector('args') — invoke an MCP connector."""

    node_type: DirectiveType = DirectiveType.MCP
    connector: str
    method: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arg: str | None = None


class CustomNode(ASTNode):
    """User-registered custom directive (e.g., @kb('query'))."""

    node_type: DirectiveType = DirectiveType.CONTEXT  # budget category: context
    directive_name: str
    raw_value: str


class MetaNode(ASTNode):
    """#key:value — execution override (model, scope, strategy, budget)."""

    node_type: DirectiveType = DirectiveType.META
    key: MetaKey
    value: str


# ---------------------------------------------------------------------------
# Resolved results
# ---------------------------------------------------------------------------


class ResolvedDirective(BaseModel):
    """A directive after resolution — contains the original node + result."""

    node: ASTNode
    content: str = ""
    source: str = ""
    relevance: float = 0.0
    error: str | None = None
    duration_ms: float = 0.0


class ContextBlock(BaseModel):
    """A block of resolved context ready for injection into the prompt."""

    source: str
    content: str
    relevance: float = 0.0
    directive_type: DirectiveType = DirectiveType.CONTEXT


class ExecutionOverrides(BaseModel):
    """Overrides extracted from # meta-directives."""

    model: str | None = None
    scope: str | None = None
    strategy: str | None = None
    budget: int | None = None


class DirectiveMetadata(BaseModel):
    """Metadata about directive resolution."""

    total_directives: int = 0
    resolved_count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    compressed: bool = False
    compression_ratio: float = 1.0
