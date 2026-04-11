# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""DirectiveEngine — main orchestrator for Sagewai prompt preprocessing.

The engine tokenizes, parses, resolves, formats, and compresses directives
so that ANY LLM (including small local models) can leverage Sagewai's full
infrastructure: Context Engine, Memory, A2A, Tools, and MCP connectors.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sagewai.directives.ast import (
    ContextBlock,
    DirectiveMetadata,
    ExecutionOverrides,
    ResolvedDirective,
)
from sagewai.directives.budget import TokenBudget, apply_budget, estimate_tokens
from sagewai.directives.compressor import compress_text
from sagewai.directives.formatter import format_context_blocks, format_tool_descriptions
from sagewai.directives.parser import extract_clean_text, parse
from sagewai.directives.profiles import ModelProfile, detect_profile
from sagewai.directives.registry import DirectiveRegistry
from sagewai.directives.file_loader import InstructionFileLoader
from sagewai.directives.resolver import DirectiveResolver
from sagewai.directives.template import parse_template
from sagewai.directives.tokenizer import has_directives

logger = logging.getLogger(__name__)


class DirectiveResult(BaseModel):
    """Result of directive resolution."""

    prompt: str
    """Final enriched prompt text with all context injected."""

    clean_prompt: str
    """User's original text with directives stripped."""

    context_blocks: list[ContextBlock]
    """Resolved context blocks for system message injection."""

    metadata: DirectiveMetadata
    """Token counts, timings, resolution stats."""

    directives_found: list[ResolvedDirective]
    """All parsed directives with their resolution results."""

    overrides: ExecutionOverrides | None = None
    """Execution overrides from # meta-directives."""

    tool_descriptions: str = ""
    """Formatted tool descriptions for prompt-based tool calling (small models)."""

    model_config = {"arbitrary_types_allowed": True}


class DirectiveEngine:
    """Preprocesses prompts by resolving directives into enriched context.

    The engine supports two syntax modes:
    - **Sigil mode** (``resolve``): ``@context('q')``, ``/tool.name('a')``, ``#model:x``
    - **Template mode** (``resolve_template``): ``{{ context.search('q') }}``

    Both modes resolve through the same pipeline: parse → resolve → format →
    compress → assemble.

    Example::

        engine = DirectiveEngine(
            context=context_engine,
            model="codellama:7b",
        )
        result = await engine.resolve(
            "@context('machine learning') Help me learn"
        )
        # result.prompt → enriched text with context injected
        # result.context_blocks → for system message injection
    """

    def __init__(
        self,
        context: Any | None = None,
        memory: Any | None = None,
        tools: dict[str, Any] | None = None,
        agents: dict[str, Any] | None = None,
        mcp_clients: dict[str, Any] | None = None,
        model: str | None = None,
        model_profile: ModelProfile | None = None,
        max_context_tokens: int | None = None,
        registry: DirectiveRegistry | None = None,
        allowed_tools: set[str] | None = None,
        allowed_mcp: set[str] | None = None,
        allow_all_tools: bool = False,
        resolution_timeout: float = 10.0,
        max_agent_depth: int = 3,
        on_agent_run: Any | None = None,
        workflows: Any | None = None,
        on_workflow_run: Any | None = None,
        instruction_loader: InstructionFileLoader | None = None,
    ) -> None:
        self._instruction_loader = instruction_loader

        # Determine model profile
        if model_profile:
            self._profile = model_profile
        elif model:
            self._profile = detect_profile(model)
        else:
            self._profile = detect_profile("gpt-4o")  # default to large

        if max_context_tokens:
            self._profile.max_context_tokens = max_context_tokens

        # Build resolver with injected services
        self._resolver = DirectiveResolver(
            context=context,
            memory=memory,
            tools=tools,
            agents=agents,
            mcp_clients=mcp_clients,
            allowed_tools=allowed_tools,
            allowed_mcp=allowed_mcp,
            allow_all_tools=allow_all_tools,
            resolution_timeout=resolution_timeout,
            max_agent_depth=max_agent_depth,
            on_agent_run=on_agent_run,
            workflows=workflows,
            on_workflow_run=on_workflow_run,
        )

        self._tools = tools or {}
        self._registry = registry or DirectiveRegistry()
        self._model = model

    @property
    def profile(self) -> ModelProfile:
        """Current model profile."""
        return self._profile

    @property
    def model(self) -> str | None:
        """Model name (if set)."""
        return self._model

    def load_instructions(self, start_dir: str | Path | None = None) -> str:
        """Load instruction files via the configured InstructionFileLoader.

        Convenience method that delegates to the loader passed at init time.
        Returns empty string if no loader is configured or no files found.

        Args:
            start_dir: Starting directory for the ancestor walk.
                Defaults to cwd.
        """
        if self._instruction_loader is None:
            return ""
        return self._instruction_loader.load(start_dir=start_dir)

    def register(self, name: str, sigil: str, handler: Any, description: str = "") -> None:
        """Register a custom directive type.

        The handler is called with the directive's raw argument string and
        should return a string (sync or async). Example::

            engine.register("kb", "@kb", search_knowledge_base)
            # Now @kb('query') works in prompts
        """
        self._registry.register(name, sigil, handler, description)
        # Also wire into the resolver's custom handlers
        self._resolver._custom_handlers[name] = handler

    # ------------------------------------------------------------------
    # Sigil mode: resolve @//#  directives in user text
    # ------------------------------------------------------------------

    async def resolve(
        self, text: str, *, user_id: str | None = None, **variables: Any
    ) -> DirectiveResult:
        """Resolve sigil directives in user text.

        Parses ``@context``, ``@memory``, ``@agent``, ``@wf``, ``/tool``,
        ``/mcp``, and ``#meta`` directives, resolves them concurrently, then
        formats and compresses the results for the target model.

        Backtick-delimited arguments (e.g., ``@agent:name(`query @datetime`)``)
        are resolved BEFORE directive tokenization — built-in variables like
        ``@datetime``, ``@date``, ``@time``, ``@user``, ``@project`` are
        replaced with their runtime values.
        """
        t0 = time.monotonic()

        # Phase 0: Resolve backtick parameters before tokenization
        from sagewai.directives.parameters import resolve_backtick_params

        text = resolve_backtick_params(text, user_id=user_id)

        # Quick check — skip if no directives found (built-in or custom)
        has_custom = bool(self._registry.match(text))
        if not has_directives(text) and not has_custom:
            return DirectiveResult(
                prompt=text,
                clean_prompt=text,
                context_blocks=[],
                metadata=DirectiveMetadata(),
                directives_found=[],
            )

        # Parse → AST (built-in directives)
        nodes = parse(text)

        # Inject custom directive nodes from registry matches
        if has_custom:
            nodes = self._inject_custom_nodes(text, nodes)

        clean_text = extract_clean_text(nodes)

        # Resolve concurrently
        resolved, context_blocks, overrides = await self._resolver.resolve_all(nodes)

        # Apply budget override if specified
        budget_total = overrides.budget or self._profile.max_context_tokens
        budget = TokenBudget(self._profile, override_total=budget_total)

        # Compress context blocks for small models
        if self._profile.compression_ratio > 1.0:
            context_blocks = self._compress_blocks(context_blocks, clean_text, budget)

        # Apply token budget
        context_blocks = apply_budget(context_blocks, budget)

        # Format for target model
        formatted = format_context_blocks(context_blocks, self._profile, clean_text)

        # Generate tool descriptions for prompt-based calling
        tool_desc = format_tool_descriptions(self._tools, self._profile)

        # Assemble final prompt
        prompt = self._assemble(formatted, tool_desc, clean_text)

        # Build metadata
        duration_ms = (time.monotonic() - t0) * 1000
        directive_nodes = [r for r in resolved if r.node.node_type.value != "text"]
        metadata = DirectiveMetadata(
            total_directives=len(directive_nodes),
            resolved_count=sum(1 for r in directive_nodes if r.content and not r.error),
            error_count=sum(1 for r in directive_nodes if r.error),
            total_duration_ms=duration_ms,
            input_tokens_estimate=estimate_tokens(text),
            output_tokens_estimate=estimate_tokens(prompt),
            compressed=self._profile.compression_ratio > 1.0,
            compression_ratio=self._profile.compression_ratio,
        )

        return DirectiveResult(
            prompt=prompt,
            clean_prompt=clean_text,
            context_blocks=context_blocks,
            metadata=metadata,
            directives_found=resolved,
            overrides=overrides if _has_overrides(overrides) else None,
            tool_descriptions=tool_desc,
        )

    # ------------------------------------------------------------------
    # Template mode: resolve {{ }} expressions in developer text
    # ------------------------------------------------------------------

    async def resolve_template(self, template: str, **variables: Any) -> DirectiveResult:
        """Resolve ``{{ }}`` template directives in developer text.

        Used for system prompts and agent configurations with template syntax.
        """
        t0 = time.monotonic()

        # Parse template → AST (same node types as sigil mode)
        nodes = parse_template(template, variables)
        clean_text = extract_clean_text(nodes)

        # Resolve through same pipeline
        resolved, context_blocks, overrides = await self._resolver.resolve_all(nodes)

        budget = TokenBudget(self._profile)

        if self._profile.compression_ratio > 1.0:
            context_blocks = self._compress_blocks(context_blocks, clean_text, budget)

        context_blocks = apply_budget(context_blocks, budget)

        # For templates, inline resolved content back into the text
        prompt = self._inline_template_results(template, nodes, resolved)

        duration_ms = (time.monotonic() - t0) * 1000
        directive_nodes = [r for r in resolved if r.node.node_type.value != "text"]
        metadata = DirectiveMetadata(
            total_directives=len(directive_nodes),
            resolved_count=sum(1 for r in directive_nodes if r.content and not r.error),
            error_count=sum(1 for r in directive_nodes if r.error),
            total_duration_ms=duration_ms,
            input_tokens_estimate=estimate_tokens(template),
            output_tokens_estimate=estimate_tokens(prompt),
        )

        return DirectiveResult(
            prompt=prompt,
            clean_prompt=clean_text,
            context_blocks=context_blocks,
            metadata=metadata,
            directives_found=resolved,
            overrides=overrides if _has_overrides(overrides) else None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inject_custom_nodes(self, text: str, nodes: list) -> list:
        """Scan text for custom directive matches and replace TextNodes."""
        from sagewai.directives.ast import CustomNode, TextNode

        matches = self._registry.match(text)
        if not matches:
            return nodes

        # Build a set of (start, end, CustomNode) from matches
        custom_spans: list[tuple[int, int, CustomNode]] = []
        for directive, m in matches:
            raw_value = m.group()
            # Extract the quoted argument from the match
            import re

            arg_m = re.search(r"""['"](.+?)['"]""", raw_value)
            arg = arg_m.group(1) if arg_m else raw_value
            custom_spans.append(
                (
                    m.start(),
                    m.end(),
                    CustomNode(directive_name=directive.name, raw_value=arg),
                )
            )

        if not custom_spans:
            return nodes

        # Re-tokenize: split TextNodes that contain custom directive matches
        result: list = []
        for node in nodes:
            if not isinstance(node, TextNode):
                result.append(node)
                continue

            # Check if any custom span falls within this text node's range
            # For simplicity, scan the text content for matches
            remaining = node.text
            for directive, m in matches:
                raw = m.group()
                if raw in remaining:
                    import re as _re

                    parts = remaining.split(raw, 1)
                    if parts[0]:
                        result.append(TextNode(text=parts[0]))
                    arg_m = _re.search(r"""['"](.+?)['"]""", raw)
                    arg = arg_m.group(1) if arg_m else raw
                    result.append(CustomNode(directive_name=directive.name, raw_value=arg))
                    remaining = parts[1] if len(parts) > 1 else ""
            if remaining:
                result.append(TextNode(text=remaining))

        return result

    def _compress_blocks(
        self,
        blocks: list[ContextBlock],
        query: str,
        budget: TokenBudget,
    ) -> list[ContextBlock]:
        """Compress context blocks using extractive summarization."""
        compressed = []
        for block in blocks:
            tokens = estimate_tokens(block.content)
            target = int(tokens / self._profile.compression_ratio)
            if tokens > target:
                content = compress_text(
                    block.content,
                    query,
                    target,
                    boost_first=self._profile.sentence_boost_first,
                    boost_last=self._profile.sentence_boost_last,
                )
                compressed.append(
                    ContextBlock(
                        source=block.source,
                        content=content,
                        relevance=block.relevance,
                        directive_type=block.directive_type,
                    )
                )
            else:
                compressed.append(block)
        return compressed

    def _assemble(self, formatted_context: str, tool_desc: str, user_text: str) -> str:
        """Assemble the final enriched prompt."""
        parts: list[str] = []
        if formatted_context:
            parts.append(formatted_context)
        if tool_desc:
            parts.append(tool_desc)
        if user_text:
            if self._profile.use_delimiters:
                parts.append(f"[USER MESSAGE]\n{user_text}")
            else:
                parts.append(user_text)
        return "\n\n".join(parts)

    def _inline_template_results(
        self,
        original: str,
        nodes: list,
        resolved: list[ResolvedDirective],
    ) -> str:
        """Replace template expressions with resolved content inline."""
        import re

        result = original
        for r in resolved:
            if r.content and not r.error and r.node.node_type.value != "text":
                # Find and replace the template expression
                # Since templates are {{ expr }}, we replace based on content
                pass

        # Simpler approach: rebuild from resolved nodes
        parts: list[str] = []
        for r in resolved:
            if r.node.node_type.value == "text":
                parts.append(r.content)
            elif r.content and not r.error:
                parts.append(r.content)
            elif r.error:
                parts.append(f"[Error: {r.error}]")
        return "".join(parts)


def _has_overrides(overrides: ExecutionOverrides) -> bool:
    """Check if any overrides are set."""
    return bool(overrides.model or overrides.scope or overrides.strategy or overrides.budget)
