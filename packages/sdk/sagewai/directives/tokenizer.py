# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tokenizer for Sagewai directive syntax.

Scans input text and produces a stream of tokens: directive tokens and plain
text segments. Handles both sigil-based directives (@, /, #) used by end-users
and {{ }} template tags used by developers.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple


class TokenKind(str, Enum):
    """Token categories produced by the tokenizer."""

    TEXT = "text"
    CONTEXT = "context"  # @context('...')
    MEMORY = "memory"  # @memory('...')
    AGENT = "agent"  # @agent:name('...')
    WORKFLOW = "workflow"  # @wf:name('...')
    TOOL = "tool"  # /tool.name('...')
    MCP = "mcp"  # /mcp.connector('...')
    META = "meta"  # #key:value
    TEMPLATE = "template"  # {{ ... }}


class Token(NamedTuple):
    """A single token from the tokenizer."""

    kind: TokenKind
    value: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# Regex patterns for sigil directives
# ---------------------------------------------------------------------------

# Match quoted strings: 'single', "double", or `backtick`, with escaped quotes
_QUOTED = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`)"""

# @context('query') or @context('query', scope='org', top_k=3)
_CONTEXT_RE = re.compile(
    rf"@context\(\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED}|\s*,\s*\w+\s*=\s*\d+)*\s*\)",
    re.DOTALL,
)

# @memory('key') or @memory('key', scope='user')
_MEMORY_RE = re.compile(
    rf"@memory\(\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*\s*\)",
    re.DOTALL,
)

# @agent:name('question')
_AGENT_RE = re.compile(
    rf"@agent:(\w[\w.-]*)\(\s*{_QUOTED}\s*\)",
    re.DOTALL,
)

# @wf:name('input') — invoke a saved workflow
_WORKFLOW_RE = re.compile(
    rf"@wf:(\w[\w.-]*)\(\s*{_QUOTED}\s*\)",
    re.DOTALL,
)

# /tool.name('args') or /tool.name(key='val', ...)
_TOOL_RE = re.compile(
    rf"/tool\.(\w[\w.-]*)\(\s*(?:{_QUOTED}|(?:\w+\s*=\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*))\s*\)",
    re.DOTALL,
)

# /mcp.connector('args') or /mcp.connector.method('args')
_MCP_RE = re.compile(
    rf"/mcp\.(\w[\w.-]*)(?:\.(\w+))?\(\s*(?:{_QUOTED}|(?:\w+\s*=\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*))\s*\)",
    re.DOTALL,
)

# #key:value — meta directives (model, scope, strategy, budget)
# Require start-of-string or whitespace before # to avoid matching inside
# URLs, code blocks, or markdown headings (e.g., "See #model:pricing-page").
_META_RE = re.compile(
    r"(?:^|(?<=\s))#(model|scope|strategy|budget):(\S+)",
)

# {{ ... }} — template expressions
_TEMPLATE_RE = re.compile(
    r"\{\{(.+?)\}\}",
    re.DOTALL,
)

# Combined pattern for scanning — order matters (longer matches first)
_DIRECTIVE_RE = re.compile(
    r"|".join(
        [
            rf"(?P<context>@context\(\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*(?:{_QUOTED}|\d+))*\s*\))",
            rf"(?P<memory>@memory\(\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*\s*\))",
            rf"(?P<agent>@agent:\w[\w.-]*\(\s*{_QUOTED}\s*\))",
            rf"(?P<workflow>@wf:\w[\w.-]*\(\s*{_QUOTED}\s*\))",
            rf"(?P<mcp>/mcp\.\w[\w.-]*(?:\.\w+)?\(\s*(?:{_QUOTED}|(?:\w+\s*=\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*))\s*\))",
            rf"(?P<tool>/tool\.\w[\w.-]*\(\s*(?:{_QUOTED}|(?:\w+\s*=\s*{_QUOTED}(?:\s*,\s*\w+\s*=\s*{_QUOTED})*))\s*\))",
            r"(?P<meta>(?:^|(?<=\s))#(?:model|scope|strategy|budget):\S+)",
            r"(?P<template>\{\{.+?\}\})",
        ]
    ),
    re.DOTALL,
)


def tokenize(text: str) -> list[Token]:
    """Tokenize input text into directive tokens and plain text segments.

    Returns a list of ``Token`` objects in order of appearance. Adjacent plain
    text is merged into single ``TEXT`` tokens.
    """
    tokens: list[Token] = []
    last_end = 0

    for match in _DIRECTIVE_RE.finditer(text):
        start, end = match.start(), match.end()

        # Emit text before this directive
        if start > last_end:
            tokens.append(Token(TokenKind.TEXT, text[last_end:start], last_end, start))

        # Determine which group matched
        raw = match.group()
        if match.group("context"):
            tokens.append(Token(TokenKind.CONTEXT, raw, start, end))
        elif match.group("memory"):
            tokens.append(Token(TokenKind.MEMORY, raw, start, end))
        elif match.group("agent"):
            tokens.append(Token(TokenKind.AGENT, raw, start, end))
        elif match.group("workflow"):
            tokens.append(Token(TokenKind.WORKFLOW, raw, start, end))
        elif match.group("mcp"):
            tokens.append(Token(TokenKind.MCP, raw, start, end))
        elif match.group("tool"):
            tokens.append(Token(TokenKind.TOOL, raw, start, end))
        elif match.group("meta"):
            tokens.append(Token(TokenKind.META, raw, start, end))
        elif match.group("template"):
            tokens.append(Token(TokenKind.TEMPLATE, raw, start, end))

        last_end = end

    # Trailing text
    if last_end < len(text):
        tokens.append(Token(TokenKind.TEXT, text[last_end:], last_end, len(text)))

    return tokens


def has_directives(text: str) -> bool:
    """Quick check whether text contains any directive syntax."""
    return _DIRECTIVE_RE.search(text) is not None
