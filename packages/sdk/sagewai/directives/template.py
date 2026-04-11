# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Template parser for developer {{ }} syntax.

Parses Jinja2-inspired template expressions in system prompts and agent
configurations. Supports:
- ``{{ context.search('query', scope='project') }}``
- ``{{ memory.user('key') }}``
- ``{{ agent.ask('name', 'question') }}``
- ``{{ tools.call('name', key='val') }}``
- ``{{ mcp.invoke('connector', 'method') }}``
- ``{{ if context.has('key') }}...{{ endif }}``
- ``{{ for item in context.list('key') }}...{{ endfor }}``
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
    TextNode,
    ToolNode,
)

# ---------------------------------------------------------------------------
# Template expression patterns
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)

# Expression patterns for resolution
_CONTEXT_SEARCH_RE = re.compile(
    r"""context\.search\(\s*['"](.+?)['"]\s*(?:,\s*scope\s*=\s*['"](\w+)['"])?\s*(?:,\s*top_k\s*=\s*(\d+))?\s*(?:,\s*tags\s*=\s*['"](.+?)['"])?\s*\)"""
)
_CONTEXT_GET_RE = re.compile(r"""context\.get\(\s*['"](.+?)['"]\s*\)""")
_CONTEXT_HAS_RE = re.compile(r"""context\.has\(\s*['"](.+?)['"]\s*\)""")
_CONTEXT_LIST_RE = re.compile(r"""context\.list\(\s*['"](.+?)['"]\s*\)""")

_MEMORY_USER_RE = re.compile(r"""memory\.user\(\s*['"](.+?)['"]\s*\)""")
_MEMORY_SESSION_RE = re.compile(r"""memory\.session\(\s*['"](.+?)['"]\s*\)""")

_AGENT_ASK_RE = re.compile(r"""agent\.ask\(\s*['"](.+?)['"]\s*,\s*['"](.+?)['"]\s*\)""")

_TOOLS_CALL_RE = re.compile(
    r"""tools\.call\(\s*['"](.+?)['"]\s*(?:,\s*(\w+)\s*=\s*['"](.+?)['"])?\s*\)"""
)

_MCP_INVOKE_RE = re.compile(
    r"""mcp\.invoke\(\s*['"](.+?)['"]\s*,\s*['"](.+?)['"]\s*(?:,\s*(.+?))?\s*\)"""
)


class TemplateParseError(Exception):
    """Raised when a template expression cannot be parsed."""
    pass


def parse_template(template: str, variables: dict[str, Any] | None = None) -> list[ASTNode]:
    """Parse a developer template string into AST nodes.

    Template expressions (``{{ ... }}``) are converted to the same AST nodes
    used by the sigil parser. Variable substitution is done for simple
    ``{{ var_name }}`` references from the ``variables`` dict.

    Control flow (``{{ if }}``, ``{{ for }}``) is handled as a preprocessing
    step — conditions and loops are evaluated, and the resulting expanded
    text is then parsed for directive expressions.
    """
    variables = variables or {}

    # Phase 1: Handle control flow (if/endif, for/endfor)
    expanded = _expand_control_flow(template, variables)

    # Phase 2: Parse directive expressions
    nodes: list[ASTNode] = []
    last_end = 0

    for match in _TEMPLATE_RE.finditer(expanded):
        start, end = match.start(), match.end()
        expr = match.group(1).strip()

        # Text before this expression
        if start > last_end:
            nodes.append(TextNode(text=expanded[last_end:start]))

        # Try to parse as a directive
        node = _parse_expression(expr, variables)
        nodes.append(node)
        last_end = end

    # Trailing text
    if last_end < len(expanded):
        nodes.append(TextNode(text=expanded[last_end:]))

    return nodes


def _parse_expression(expr: str, variables: dict[str, Any]) -> ASTNode:
    """Parse a single template expression into an AST node."""

    # context.search('query', scope='project', top_k=5, tags='a,b')
    m = _CONTEXT_SEARCH_RE.match(expr)
    if m:
        tags_raw = m.group(4)
        tags = [t.strip() for t in tags_raw.split(",")] if tags_raw else None
        return ContextNode(
            query=m.group(1),
            scope=m.group(2),
            top_k=int(m.group(3)) if m.group(3) else 5,
            tags=tags,
        )

    # context.get('key')
    m = _CONTEXT_GET_RE.match(expr)
    if m:
        return ContextNode(query=m.group(1), top_k=1)

    # memory.user('key') / memory.session('key')
    m = _MEMORY_USER_RE.match(expr)
    if m:
        return MemoryNode(query=m.group(1), scope="project")

    m = _MEMORY_SESSION_RE.match(expr)
    if m:
        return MemoryNode(query=m.group(1), scope="session")

    # agent.ask('name', 'question')
    m = _AGENT_ASK_RE.match(expr)
    if m:
        return AgentNode(agent_name=m.group(1), question=m.group(2))

    # tools.call('name', key='val')
    m = _TOOLS_CALL_RE.match(expr)
    if m:
        args = {}
        if m.group(2) and m.group(3):
            args[m.group(2)] = m.group(3)
        return ToolNode(tool_name=m.group(1), arguments=args)

    # mcp.invoke('connector', 'method', args)
    m = _MCP_INVOKE_RE.match(expr)
    if m:
        return McpNode(
            connector=m.group(1),
            method=m.group(2),
            raw_arg=m.group(3),
        )

    # Simple variable substitution
    if expr in variables:
        return TextNode(text=str(variables[expr]))

    # Unknown expression — pass through as text
    return TextNode(text=f"{{{{ {expr} }}}}")


# ---------------------------------------------------------------------------
# Control flow expansion
# ---------------------------------------------------------------------------

_IF_RE = re.compile(
    r"\{\{\s*if\s+(.+?)\s*\}\}(.*?)\{\{\s*endif\s*\}\}",
    re.DOTALL,
)

_FOR_RE = re.compile(
    r"\{\{\s*for\s+(\w+)\s+in\s+(.+?)\s*\}\}(.*?)\{\{\s*endfor\s*\}\}",
    re.DOTALL,
)


def _expand_control_flow(template: str, variables: dict[str, Any]) -> str:
    """Expand if/endif and for/endfor blocks.

    This is a simple preprocessing step — conditions are evaluated against
    the variables dict. Context/memory checks (e.g., ``context.has('key')``)
    are deferred and always evaluate to True (the content will be resolved
    later; if empty, it naturally contributes nothing).

    **Limitations (v1):**

    - **Nested control flow is not supported.** ``{{ if }}...{{ if }}...{{ endif
      }}...{{ endif }}`` will match incorrectly because the regex uses
      non-greedy matching that binds the first ``{{ endif }}`` to the inner
      ``{{ if }}``, leaving the outer block unclosed. Flatten nested conditions
      into separate blocks or use the directive syntax directly.

    - **``context.has()`` always evaluates to True** because the actual context
      retrieval hasn't happened yet at template expansion time. If the key
      doesn't exist, the resolved content will simply be empty — the block is
      still included but contributes no content. For strict conditional
      inclusion, use the ``DirectiveEngine`` resolution phase directly.
    """
    result = template

    # Expand if blocks
    def _replace_if(match: re.Match) -> str:
        condition = match.group(1).strip()
        body = match.group(2)

        # Check simple variable truthiness
        if condition in variables:
            return body if variables[condition] else ""

        # context.has() — assume True (deferred resolution)
        if _CONTEXT_HAS_RE.match(condition):
            return body

        # Default: include body (optimistic)
        return body

    result = _IF_RE.sub(_replace_if, result)

    # Expand for blocks
    def _replace_for(match: re.Match) -> str:
        var_name = match.group(1)
        iterable_expr = match.group(2).strip()
        body = match.group(3)

        # Check if iterable is a variable
        if iterable_expr in variables:
            items = variables[iterable_expr]
            if not hasattr(items, "__iter__"):
                return ""
            parts = []
            for item in items:
                parts.append(body.replace(f"{{{{ {var_name} }}}}", str(item)))
            return "".join(parts)

        # context.list() — can't expand statically, keep single iteration
        return body

    result = _FOR_RE.sub(_replace_for, result)

    return result
