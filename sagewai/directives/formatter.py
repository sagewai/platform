# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Model-profile-aware output formatter.

Formats resolved context blocks differently based on the target model's
capabilities. Small models get structured delimiters and explicit instructions.
Large models get natural, minimal formatting.
"""

from __future__ import annotations

from sagewai.directives.ast import ContextBlock, DirectiveType
from sagewai.directives.profiles import ModelProfile


def format_context_blocks(
    blocks: list[ContextBlock],
    profile: ModelProfile,
    user_message: str = "",
) -> str:
    """Format resolved context blocks for prompt injection.

    The output style varies by model profile:
    - **small**: Heavy structure with ``[CONTEXT]``, ``[SOURCE]`` delimiters,
      explicit instructions to use the context.
    - **medium**: Light delimiters, no explicit instructions.
    - **large**: Natural language, minimal structure.
    """
    if not blocks:
        return ""

    if profile.use_delimiters:
        return _format_delimited(blocks, profile, user_message)
    else:
        return _format_natural(blocks)


def format_tool_descriptions(
    tools: dict[str, object],
    profile: ModelProfile,
) -> str:
    """Format available tools for prompt-based tool calling (small models).

    Only used when ``profile.tool_call_mode == 'prompt_based'``.
    For ``native`` mode, tools are passed via the LLM's function calling API.
    """
    if profile.tool_call_mode != "prompt_based" or not tools:
        return ""

    lines = ["[AVAILABLE TOOLS — Call by outputting JSON]"]
    for name, spec in tools.items():
        desc = getattr(spec, "description", "") or ""
        params = getattr(spec, "parameters", {}) or {}
        props = params.get("properties", {})
        param_str = ", ".join(
            f'"{k}": "{v.get("type", "string")}"'
            for k, v in props.items()
        )
        lines.append(f'{{"name": "{name}", "description": "{desc}", "parameters": {{{param_str}}}}}')

    lines.append("")
    lines.append("To use a tool, output EXACTLY:")
    lines.append('TOOL_CALL: {"name": "tool_name", "arguments": {"key": "value"}}')
    lines.append("")
    lines.append("After the tool result is provided, continue your response.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting strategies
# ---------------------------------------------------------------------------


def _format_delimited(
    blocks: list[ContextBlock],
    profile: ModelProfile,
    user_message: str,
) -> str:
    """Structured formatting with clear delimiters (small/medium models)."""
    sections: list[str] = []

    # Group blocks by type
    context_blocks = [b for b in blocks if b.directive_type in (DirectiveType.CONTEXT, DirectiveType.MEMORY)]
    tool_blocks = [b for b in blocks if b.directive_type == DirectiveType.TOOL]
    agent_blocks = [b for b in blocks if b.directive_type == DirectiveType.AGENT]
    mcp_blocks = [b for b in blocks if b.directive_type == DirectiveType.MCP]

    if context_blocks:
        section_lines = ["[SYSTEM CONTEXT — READ AND USE THIS INFORMATION]", ""]
        for block in context_blocks:
            relevance_pct = f" | Relevance: {block.relevance:.2f}" if block.relevance > 0 else ""
            section_lines.append(f"[SOURCE: {block.source}{relevance_pct}]")
            section_lines.append(block.content)
            section_lines.append("")
        section_lines.append("[END CONTEXT]")
        sections.append("\n".join(section_lines))

    if tool_blocks:
        section_lines = ["[TOOL RESULTS]", ""]
        for block in tool_blocks:
            section_lines.append(f"[TOOL: {block.source}]")
            section_lines.append(block.content)
            section_lines.append("")
        section_lines.append("[END TOOL RESULTS]")
        sections.append("\n".join(section_lines))

    if agent_blocks:
        section_lines = ["[AGENT RESPONSES]", ""]
        for block in agent_blocks:
            section_lines.append(f"[AGENT: {block.source}]")
            section_lines.append(block.content)
            section_lines.append("")
        section_lines.append("[END AGENT RESPONSES]")
        sections.append("\n".join(section_lines))

    if mcp_blocks:
        section_lines = ["[EXTERNAL DATA]", ""]
        for block in mcp_blocks:
            section_lines.append(f"[SOURCE: {block.source}]")
            section_lines.append(block.content)
            section_lines.append("")
        section_lines.append("[END EXTERNAL DATA]")
        sections.append("\n".join(section_lines))

    # Add explicit instructions for small models
    if profile.use_explicit_instructions:
        sections.append(
            "[INSTRUCTIONS]\n"
            "You MUST use the context provided above to answer the user's question.\n"
            "If the context does not contain the answer, say so clearly.\n"
            "Respond step by step. Use simple, clear language."
        )

    return "\n\n".join(sections)


def _format_natural(blocks: list[ContextBlock]) -> str:
    """Minimal natural language formatting (large models)."""
    parts: list[str] = []
    for block in blocks:
        if block.directive_type in (DirectiveType.CONTEXT, DirectiveType.MEMORY):
            parts.append(f"Relevant context ({block.source}):\n{block.content}")
        elif block.directive_type == DirectiveType.TOOL:
            parts.append(f"Tool result ({block.source}):\n{block.content}")
        elif block.directive_type == DirectiveType.AGENT:
            parts.append(f"Agent response ({block.source}):\n{block.content}")
        elif block.directive_type == DirectiveType.MCP:
            parts.append(f"External data ({block.source}):\n{block.content}")
    return "\n\n".join(parts)


def parse_tool_call_from_output(text: str) -> tuple[str, dict] | None:
    """Parse a TOOL_CALL from model output (prompt-based tool calling).

    Returns ``(tool_name, arguments)`` or ``None`` if no tool call found.
    Used by the execution strategy when ``tool_call_mode == 'prompt_based'``.

    .. note::
        This parser is not yet wired into the execution strategy or
        ``BaseAgent._call_llm()``. Small models outputting ``TOOL_CALL: {...}``
        will not automatically execute the tool in v1. Integration into the
        ReActStrategy loop is planned for a follow-up PR.
        TODO(directive-engine): Wire into ReActStrategy for prompt-based tool
        calling on small models.
    """
    import json

    idx = text.find("TOOL_CALL:")
    if idx == -1:
        return None

    # Find the JSON object after TOOL_CALL: by matching balanced braces
    rest = text[idx + len("TOOL_CALL:"):].lstrip()
    if not rest.startswith("{"):
        return None

    depth = 0
    end = 0
    for i, ch in enumerate(rest):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == 0:
        return None

    try:
        data = json.loads(rest[:end])
        name = data.get("name", "")
        args = data.get("arguments", {})
        if name:
            return name, args
    except (json.JSONDecodeError, KeyError):
        pass

    return None
