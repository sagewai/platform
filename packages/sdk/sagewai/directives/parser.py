# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parser for Sagewai directive tokens.

Converts a token stream from the tokenizer into an AST of directive nodes.
Each directive token is parsed into its corresponding AST node with extracted
arguments. Plain text tokens become ``TextNode``s.
"""

from __future__ import annotations

import re
from typing import Any

from sagewai.directives.ast import (
    ASTNode,
    AgentNode,
    ContextNode,
    McpNode,
    MemoryNode,
    MetaKey,
    MetaNode,
    TextNode,
    ToolNode,
    WorkflowNode,
)
from sagewai.directives.tokenizer import Token, TokenKind, tokenize


class DirectiveParseError(Exception):
    """Raised when a directive cannot be parsed."""

    def __init__(self, message: str, token: Token | None = None) -> None:
        self.token = token
        pos = f" at position {token.start}" if token else ""
        super().__init__(f"{message}{pos}")


# ---------------------------------------------------------------------------
# Argument extraction helpers
# ---------------------------------------------------------------------------

_UNQUOTE_RE = re.compile(r"""^['"`](.*)['"`]$""", re.DOTALL)
_KWARGS_RE = re.compile(
    r"""(\w+)\s*=\s*(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)"|(\d+))"""
)


def _unquote(s: str) -> str:
    """Remove surrounding quotes from a string."""
    m = _UNQUOTE_RE.match(s.strip())
    return m.group(1) if m else s.strip()


def _extract_first_quoted(text: str) -> str:
    """Extract the first quoted string from a parenthesized expression."""
    m = re.search(r"""['"`](.+?)['"`]""", text, re.DOTALL)
    return m.group(1) if m else text


def _extract_kwargs(text: str) -> dict[str, Any]:
    """Extract keyword arguments from a parenthesized expression."""
    kwargs: dict[str, Any] = {}
    for m in _KWARGS_RE.finditer(text):
        key = m.group(1)
        val: Any = m.group(2) or m.group(3) or m.group(4)
        if m.group(4):
            val = int(val)
        kwargs[key] = val
    return kwargs


# ---------------------------------------------------------------------------
# Token → AST node parsers
# ---------------------------------------------------------------------------


def _parse_context(token: Token) -> ContextNode:
    """Parse @context('query', scope='org', top_k=3, tags='a,b')."""
    query = _extract_first_quoted(token.value)
    kwargs = _extract_kwargs(token.value)
    # Remove the positional query from kwargs if present
    kwargs.pop("query", None)
    tags_raw = kwargs.get("tags")
    tags = [t.strip() for t in tags_raw.split(",")] if tags_raw else None
    return ContextNode(
        query=query,
        scope=kwargs.get("scope"),
        top_k=kwargs.get("top_k", 5),
        tags=tags,
    )


def _parse_memory(token: Token) -> MemoryNode:
    """Parse @memory('key', scope='user')."""
    query = _extract_first_quoted(token.value)
    kwargs = _extract_kwargs(token.value)
    return MemoryNode(query=query, scope=kwargs.get("scope"))


def _parse_agent(token: Token) -> AgentNode:
    """Parse @agent:name('question')."""
    m = re.match(r"@agent:(\w[\w.-]*)\(\s*", token.value)
    if not m:
        raise DirectiveParseError("Invalid agent directive", token)
    agent_name = m.group(1)
    question = _extract_first_quoted(token.value[m.end() :])
    return AgentNode(agent_name=agent_name, question=question)


def _parse_workflow(token: Token) -> WorkflowNode:
    """Parse @wf:name('input')."""
    m = re.match(r"@wf:(\w[\w.-]*)\(\s*", token.value)
    if not m:
        raise DirectiveParseError("Invalid workflow directive", token)
    workflow_name = m.group(1)
    input_text = _extract_first_quoted(token.value[m.end() :])
    return WorkflowNode(workflow_name=workflow_name, input_text=input_text)


def _parse_tool(token: Token) -> ToolNode:
    """Parse /tool.name('args') or /tool.name(key='val')."""
    m = re.match(r"/tool\.(\w[\w.-]*)\(", token.value)
    if not m:
        raise DirectiveParseError("Invalid tool directive", token)
    tool_name = m.group(1)
    inner = token.value[m.end() :]

    kwargs = _extract_kwargs(inner)
    raw_arg = _extract_first_quoted(inner) if not kwargs else None

    return ToolNode(tool_name=tool_name, arguments=kwargs, raw_arg=raw_arg)


def _parse_mcp(token: Token) -> McpNode:
    """Parse /mcp.connector('args') or /mcp.connector.method('args')."""
    m = re.match(r"/mcp\.(\w[\w.-]*)(?:\.(\w+))?\(", token.value)
    if not m:
        raise DirectiveParseError("Invalid MCP directive", token)
    connector = m.group(1)
    method = m.group(2)
    inner = token.value[m.end() :]

    kwargs = _extract_kwargs(inner)
    raw_arg = _extract_first_quoted(inner) if not kwargs else None

    return McpNode(
        connector=connector,
        method=method,
        arguments=kwargs,
        raw_arg=raw_arg,
    )


_KNOWN_STRATEGIES = {
    "react",
    "planning",
    "lats",
    "tree-of-thoughts",
    "self-correction",
    "reflexion",
    "routing",
    "chain-of-thought",
    "majority-vote",
    "debate",
    "evaluator-optimizer",
}


def _parse_meta(token: Token) -> MetaNode:
    """Parse #key:value with validation."""
    m = re.match(r"#(\w+):(\S+)", token.value.strip())
    if not m:
        raise DirectiveParseError("Invalid meta directive", token)
    key_str = m.group(1)
    value = m.group(2)
    try:
        key = MetaKey(key_str)
    except ValueError:
        raise DirectiveParseError(f"Unknown meta key: {key_str}", token)

    # Validate values
    if key == MetaKey.BUDGET:
        try:
            budget_val = int(value)
            if budget_val <= 0:
                raise DirectiveParseError(
                    f"Budget must be a positive integer, got: {value}",
                    token,
                )
        except ValueError:
            raise DirectiveParseError(
                f"Budget must be a positive integer, got: {value}",
                token,
            )

    if key == MetaKey.STRATEGY and value not in _KNOWN_STRATEGIES:
        raise DirectiveParseError(
            f"Unknown strategy: {value!r}. Known strategies: {sorted(_KNOWN_STRATEGIES)}",
            token,
        )

    if key == MetaKey.MODEL and not value:
        raise DirectiveParseError("Model name cannot be empty", token)

    return MetaNode(key=key, value=value)


# ---------------------------------------------------------------------------
# Token kind → parser dispatch
# ---------------------------------------------------------------------------

_PARSERS = {
    TokenKind.CONTEXT: _parse_context,
    TokenKind.MEMORY: _parse_memory,
    TokenKind.AGENT: _parse_agent,
    TokenKind.WORKFLOW: _parse_workflow,
    TokenKind.TOOL: _parse_tool,
    TokenKind.MCP: _parse_mcp,
    TokenKind.META: _parse_meta,
}


def parse(text: str) -> list[ASTNode]:
    """Parse input text into a list of AST nodes.

    This is the main entry point: tokenizes the text, then converts each token
    into the corresponding AST node.

    Raises ``DirectiveParseError`` for malformed directives.
    """
    tokens = tokenize(text)
    nodes: list[ASTNode] = []

    for token in tokens:
        if token.kind == TokenKind.TEXT:
            nodes.append(TextNode(text=token.value))
        elif token.kind == TokenKind.TEMPLATE:
            # Template tokens are passed through for the template parser
            nodes.append(TextNode(text=token.value))
        else:
            parser_fn = _PARSERS.get(token.kind)
            if parser_fn:
                nodes.append(parser_fn(token))
            else:
                nodes.append(TextNode(text=token.value))

    return nodes


def extract_clean_text(nodes: list[ASTNode]) -> str:
    """Extract the plain text from an AST, stripping all directives."""
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, TextNode):
            parts.append(node.text)
    return "".join(parts).strip()
