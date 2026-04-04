# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Directive resolver — walks AST nodes and resolves them concurrently.

Each directive type has a resolver that calls the corresponding Sagewai service
(Context Engine, Memory, Tools, A2A, MCP). Resolution is concurrent where
possible using ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol, runtime_checkable

from sagewai.directives.ast import (
    ASTNode,
    AgentNode,
    ContextBlock,
    ContextNode,
    CustomNode,
    DirectiveType,
    ExecutionOverrides,
    McpNode,
    MemoryNode,
    MetaKey,
    MetaNode,
    ResolvedDirective,
    TextNode,
    ToolNode,
    WorkflowNode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service protocols (duck-typed for loose coupling)
# ---------------------------------------------------------------------------


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for context/memory retrieval."""

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]: ...


@runtime_checkable
class AgentProvider(Protocol):
    """Protocol for A2A agent delegation."""

    async def chat(self, message: str) -> str: ...


@runtime_checkable
class ToolHandler(Protocol):
    """Protocol for tool execution."""

    async def __call__(self, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class DirectiveResolver:
    """Resolves AST nodes by calling Sagewai services.

    Services are injected at construction time. Missing services cause graceful
    degradation — the directive is resolved with an error message instead of
    raising an exception.

    **Security:** Tool and MCP directives are gated by an allowlist. Only tools
    and connectors explicitly marked as directive-safe are executable from user
    prompts. Set ``allowed_tools`` / ``allowed_mcp`` to control access, or pass
    ``allow_all_tools=True`` to disable the gate (development only).
    """

    def __init__(
        self,
        context: Any | None = None,
        memory: Any | None = None,
        tools: dict[str, Any] | None = None,
        agents: dict[str, Any] | None = None,
        mcp_clients: dict[str, Any] | None = None,
        custom_handlers: dict[str, Any] | None = None,
        allowed_tools: set[str] | None = None,
        allowed_mcp: set[str] | None = None,
        allow_all_tools: bool = False,
        resolution_timeout: float = 10.0,
        max_agent_depth: int = 3,
        on_agent_run: Any | None = None,
        workflows: Any | None = None,
        on_workflow_run: Any | None = None,
    ) -> None:
        self._context = context
        self._memory = memory
        self._tools = tools or {}
        self._agents = agents or {}
        self._mcp_clients = mcp_clients or {}
        self._custom_handlers = custom_handlers or {}
        self._allowed_tools = allowed_tools
        self._allowed_mcp = allowed_mcp
        self._allow_all_tools = allow_all_tools
        self._on_agent_run = (
            on_agent_run  # callback(agent_name, question, response, duration_ms, tokens)
        )
        self._workflows = workflows  # SavedWorkflowStore or dict-like
        self._on_workflow_run = on_workflow_run
        self._resolution_timeout = resolution_timeout
        self._max_agent_depth = max_agent_depth

    async def resolve_all(
        self, nodes: list[ASTNode]
    ) -> tuple[list[ResolvedDirective], list[ContextBlock], ExecutionOverrides]:
        """Resolve all directive nodes concurrently.

        Returns:
            resolved: List of resolved directives with content.
            context_blocks: Merged context blocks for prompt injection.
            overrides: Execution overrides from meta-directives.
        """
        overrides = ExecutionOverrides()
        resolvable: list[tuple[int, ASTNode]] = []
        results: list[ResolvedDirective] = []

        # First pass: extract meta-directives (instant, no async)
        for i, node in enumerate(nodes):
            if isinstance(node, MetaNode):
                _apply_meta(node, overrides)
                results.append(
                    ResolvedDirective(
                        node=node,
                        content="",
                        source=f"meta:{node.key.value}",
                    )
                )
            elif isinstance(node, TextNode):
                results.append(ResolvedDirective(node=node, content=node.text))
            else:
                resolvable.append((len(results), node))
                results.append(ResolvedDirective(node=node))  # placeholder

        logger.info(
            "[RESOLVER] Parsed %d nodes: %d text, %d meta, %d resolvable: %s",
            len(nodes),
            sum(1 for n in nodes if isinstance(n, TextNode)),
            sum(1 for n in nodes if isinstance(n, MetaNode)),
            len(resolvable),
            [
                (type(n).__name__, getattr(n, "agent_name", getattr(n, "query", "?")))
                for _, n in resolvable
            ],
        )

        # Second pass: resolve all resolvable directives concurrently (with timeout)
        if resolvable:
            tasks = [self._resolve_one(node) for _, node in resolvable]
            try:
                resolved = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._resolution_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Directive resolution timed out after %.1fs", self._resolution_timeout
                )
                resolved = [
                    asyncio.TimeoutError(f"Resolution timed out after {self._resolution_timeout}s")
                    for _ in resolvable
                ]

            for (idx, _node), result in zip(resolvable, resolved):
                if isinstance(result, Exception):
                    results[idx] = ResolvedDirective(
                        node=_node,
                        error=str(result),
                        source=f"error:{type(result).__name__}",
                    )
                else:
                    results[idx] = result

        # Build context blocks from resolved directives
        context_blocks = _build_context_blocks(results)

        return results, context_blocks, overrides

    async def _resolve_one(self, node: ASTNode) -> ResolvedDirective:
        """Resolve a single directive node."""
        t0 = time.monotonic()

        if isinstance(node, CustomNode):
            result = await self._resolve_custom(node)
        elif isinstance(node, ContextNode):
            result = await self._resolve_context(node)
        elif isinstance(node, MemoryNode):
            result = await self._resolve_memory(node)
        elif isinstance(node, AgentNode):
            result = await self._resolve_agent(node)
        elif isinstance(node, WorkflowNode):
            result = await self._resolve_workflow(node)
        elif isinstance(node, ToolNode):
            result = await self._resolve_tool(node)
        elif isinstance(node, McpNode):
            result = await self._resolve_mcp(node)
        else:
            result = ResolvedDirective(node=node, error=f"Unknown node type: {type(node)}")

        result.duration_ms = (time.monotonic() - t0) * 1000
        return result

    # ------------------------------------------------------------------
    # Per-type resolvers
    # ------------------------------------------------------------------

    async def _resolve_context(self, node: ContextNode) -> ResolvedDirective:
        if not self._context:
            return ResolvedDirective(
                node=node,
                error="No context provider configured",
                source="context",
            )

        try:
            if node.scope or node.tags:
                scopes = None
                if node.scope:
                    try:
                        from sagewai.context.models import ContextScope

                        scopes = [ContextScope(node.scope)]
                    except (ValueError, KeyError):
                        pass
                results = await self._context.search(
                    node.query, top_k=node.top_k, scopes=scopes, tags=node.tags,
                )
                items = [r.content for r in results]
            else:
                items = await self._context.retrieve(node.query, top_k=node.top_k)
            content = "\n\n".join(items) if items else ""
            return ResolvedDirective(
                node=node,
                content=content,
                source="context_engine",
                relevance=0.9 if content else 0.0,
            )
        except Exception as e:
            logger.warning("Context resolution failed: %s", e)
            return ResolvedDirective(node=node, error=str(e), source="context")

    async def _resolve_memory(self, node: MemoryNode) -> ResolvedDirective:
        provider = self._memory or self._context
        if not provider:
            return ResolvedDirective(
                node=node,
                error="No memory provider configured",
                source="memory",
            )

        try:
            items = await provider.retrieve(node.query)
            content = "\n\n".join(items) if items else ""
            return ResolvedDirective(
                node=node,
                content=content,
                source="memory",
                relevance=0.8 if content else 0.0,
            )
        except Exception as e:
            logger.warning("Memory resolution failed: %s", e)
            return ResolvedDirective(node=node, error=str(e), source="memory")

    async def _resolve_agent(
        self,
        node: AgentNode,
        _call_stack: tuple[str, ...] = (),
    ) -> ResolvedDirective:
        # Circular reference detection
        if node.agent_name in _call_stack:
            chain = " → ".join([*_call_stack, node.agent_name])
            logger.warning("Circular agent reference detected: %s", chain)
            return ResolvedDirective(
                node=node,
                error=f"Circular agent reference detected: {chain}",
                source=f"agent:{node.agent_name}",
            )

        # Max depth check
        if len(_call_stack) >= self._max_agent_depth:
            return ResolvedDirective(
                node=node,
                error=f"Agent delegation depth exceeded (max {self._max_agent_depth}): {' → '.join(_call_stack)}",
                source=f"agent:{node.agent_name}",
            )

        logger.info(
            "[AGENT DIRECTIVE] Looking up agent %r in registry (type=%s)",
            node.agent_name,
            type(self._agents).__name__,
        )
        agent = self._agents.get(node.agent_name)
        logger.info("[AGENT DIRECTIVE] Lookup result: %s", agent is not None)
        if not agent:
            logger.warning("[AGENT DIRECTIVE] Agent not found: %s", node.agent_name)
            return ResolvedDirective(
                node=node,
                error=f"Agent not found: {node.agent_name}",
                source=f"agent:{node.agent_name}",
            )

        try:
            logger.info(
                "[AGENT DIRECTIVE] Delegating to agent %r: %s", node.agent_name, node.question[:80]
            )
            started_at = time.time()
            response = await agent.chat(node.question)
            duration_ms = (time.time() - started_at) * 1000

            # Extract token counts from the agent's last run if available
            total_tokens = 0
            if hasattr(agent, "_last_run_messages"):
                # Sum tokens from the agent's cost tracker or config
                total_tokens = getattr(agent, "_last_total_tokens", 0)

            # Notify the admin layer so the run is tracked, costed, and auditable
            if self._on_agent_run is not None:
                try:
                    self._on_agent_run(
                        agent_name=node.agent_name,
                        input_text=node.question,
                        output_text=response,
                        status="completed",
                        total_tokens=total_tokens,
                        started_at=started_at,
                        completed_at=time.time(),
                        delegation_source="directive",
                    )
                except Exception as cb_err:
                    logger.warning("on_agent_run callback failed: %s", cb_err)

            return ResolvedDirective(
                node=node,
                content=response,
                source=f"agent:{node.agent_name}",
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("Agent %s resolution failed: %s", node.agent_name, e)

            # Record failed delegation too
            if self._on_agent_run is not None:
                try:
                    self._on_agent_run(
                        agent_name=node.agent_name,
                        input_text=node.question,
                        output_text="",
                        status="failed",
                        total_tokens=0,
                        started_at=time.time(),
                        completed_at=time.time(),
                        delegation_source="directive",
                        error=str(e),
                    )
                except Exception:
                    pass

            return ResolvedDirective(
                node=node,
                error=str(e),
                source=f"agent:{node.agent_name}",
            )

    async def _resolve_workflow(self, node: WorkflowNode) -> ResolvedDirective:
        """Resolve @wf:name('input') — look up and execute a saved workflow."""
        if not self._workflows:
            return ResolvedDirective(
                node=node,
                error="No workflow store configured",
                source=f"workflow:{node.workflow_name}",
            )

        try:
            # Look up workflow by name from the registry
            wf = await self._workflows.get_by_name(node.workflow_name)
            if not wf:
                return ResolvedDirective(
                    node=node,
                    error=f"Workflow not found in registry: {node.workflow_name}",
                    source=f"workflow:{node.workflow_name}",
                )

            logger.info(
                "[WORKFLOW DIRECTIVE] Executing workflow %r with input: %s",
                node.workflow_name,
                node.input_text[:80],
            )

            started_at = time.time()

            # Parse the YAML and build the workflow
            from sagewai.core.yaml_workflow import load_workflow_string

            # Use agent registry to resolve ref agents if available
            agent_resolver = None
            if self._agents:

                def agent_resolver(name: str) -> Any:
                    return self._agents.get(name)

            spec = load_workflow_string(wf.yaml_content, agent_resolver=agent_resolver)
            raw_result = await spec.run(node.input_text)

            duration_ms = (time.time() - started_at) * 1000
            output = str(raw_result) if raw_result is not None else ""

            # Track via callback
            if self._on_workflow_run is not None:
                try:
                    self._on_workflow_run(
                        workflow_name=node.workflow_name,
                        input_text=node.input_text,
                        output_text=output,
                        status="completed",
                        started_at=started_at,
                        completed_at=time.time(),
                        delegation_source="directive",
                    )
                except Exception as cb_err:
                    logger.warning("on_workflow_run callback failed: %s", cb_err)

            return ResolvedDirective(
                node=node,
                content=output,
                source=f"workflow:{node.workflow_name}",
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("Workflow %s resolution failed: %s", node.workflow_name, e)

            if self._on_workflow_run is not None:
                try:
                    self._on_workflow_run(
                        workflow_name=node.workflow_name,
                        input_text=node.input_text,
                        output_text="",
                        status="failed",
                        started_at=time.time(),
                        completed_at=time.time(),
                        delegation_source="directive",
                        error=str(e),
                    )
                except Exception as cb_err:
                    logger.warning("on_workflow_run callback failed: %s", cb_err)

            return ResolvedDirective(
                node=node,
                error=str(e),
                source=f"workflow:{node.workflow_name}",
            )

    async def _resolve_tool(self, node: ToolNode) -> ResolvedDirective:
        # Security gate: only allowlisted tools can be invoked from directives
        if not self._allow_all_tools:
            if self._allowed_tools is not None and node.tool_name not in self._allowed_tools:
                logger.warning("Directive blocked: tool %r not in allowed_tools", node.tool_name)
                return ResolvedDirective(
                    node=node,
                    error=f"Tool not allowed via directives: {node.tool_name}",
                    source=f"tool:{node.tool_name}",
                )
            elif self._allowed_tools is None:
                # No allowlist configured — block all tool directives by default
                logger.warning(
                    "Directive blocked: no allowed_tools configured, tool %r denied",
                    node.tool_name,
                )
                return ResolvedDirective(
                    node=node,
                    error=(
                        f"Tool directives disabled: configure allowed_tools "
                        f"or set allow_all_tools=True"
                    ),
                    source=f"tool:{node.tool_name}",
                )

        tool_spec = self._tools.get(node.tool_name)
        if not tool_spec:
            return ResolvedDirective(
                node=node,
                error=f"Tool not found: {node.tool_name}",
                source=f"tool:{node.tool_name}",
            )

        logger.info("Directive executing tool: %s args=%s", node.tool_name, node.arguments)
        try:
            handler = getattr(tool_spec, "handler", None)
            if handler is None:
                return ResolvedDirective(
                    node=node,
                    error=f"Tool has no handler: {node.tool_name}",
                    source=f"tool:{node.tool_name}",
                )

            args = node.arguments
            if not args and node.raw_arg:
                # Single positional argument — pass as 'query' or first param
                params = getattr(tool_spec, "parameters", {})
                props = params.get("properties", {})
                if props:
                    first_param = next(iter(props))
                    args = {first_param: node.raw_arg}
                else:
                    args = {"query": node.raw_arg}

            if asyncio.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)

            content = str(result) if result is not None else ""
            return ResolvedDirective(
                node=node,
                content=content,
                source=f"tool:{node.tool_name}",
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("Tool %s execution failed: %s", node.tool_name, e)
            return ResolvedDirective(
                node=node,
                error=str(e),
                source=f"tool:{node.tool_name}",
            )

    async def _resolve_mcp(self, node: McpNode) -> ResolvedDirective:
        # Security gate: same as tools — allowlist required
        if not self._allow_all_tools:
            if self._allowed_mcp is not None and node.connector not in self._allowed_mcp:
                logger.warning(
                    "Directive blocked: MCP connector %r not in allowed_mcp", node.connector
                )
                return ResolvedDirective(
                    node=node,
                    error=f"MCP connector not allowed via directives: {node.connector}",
                    source=f"mcp:{node.connector}",
                )
            elif self._allowed_mcp is None:
                logger.warning(
                    "Directive blocked: no allowed_mcp configured, connector %r denied",
                    node.connector,
                )
                return ResolvedDirective(
                    node=node,
                    error=(
                        f"MCP directives disabled: configure allowed_mcp "
                        f"or set allow_all_tools=True"
                    ),
                    source=f"mcp:{node.connector}",
                )

        logger.info("Directive executing MCP: %s method=%s", node.connector, node.method)
        client = self._mcp_clients.get(node.connector)
        if not client:
            return ResolvedDirective(
                node=node,
                error=f"MCP connector not found: {node.connector}",
                source=f"mcp:{node.connector}",
            )

        try:
            # MCP tool call — find the right tool on the client
            tools = getattr(client, "tools", None) or {}
            method = node.method or "default"

            if hasattr(client, "call_tool"):
                args = node.arguments
                if not args and node.raw_arg:
                    args = {"query": node.raw_arg}
                result = await client.call_tool(method, args)
                content = str(result) if result is not None else ""
            else:
                content = ""
                return ResolvedDirective(
                    node=node,
                    error=f"MCP client does not support call_tool",
                    source=f"mcp:{node.connector}",
                )

            return ResolvedDirective(
                node=node,
                content=content,
                source=f"mcp:{node.connector}:{method}",
                relevance=1.0,
            )
        except Exception as e:
            logger.warning("MCP %s resolution failed: %s", node.connector, e)
            return ResolvedDirective(
                node=node,
                error=str(e),
                source=f"mcp:{node.connector}",
            )

    async def _resolve_custom(self, node: CustomNode) -> ResolvedDirective:
        handler = self._custom_handlers.get(node.directive_name)
        if not handler:
            return ResolvedDirective(
                node=node,
                error=f"No handler for custom directive: {node.directive_name}",
                source=f"custom:{node.directive_name}",
            )

        try:
            import asyncio
            import inspect

            if inspect.iscoroutinefunction(handler):
                result = await handler(node.raw_value)
            elif asyncio.iscoroutinefunction(handler):
                result = await handler(node.raw_value)
            else:
                result = handler(node.raw_value)

            content = str(result) if result is not None else ""
            return ResolvedDirective(
                node=node,
                content=content,
                source=f"custom:{node.directive_name}",
                relevance=0.9 if content else 0.0,
            )
        except Exception as e:
            logger.warning("Custom directive %s failed: %s", node.directive_name, e)
            return ResolvedDirective(
                node=node,
                error=str(e),
                source=f"custom:{node.directive_name}",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_meta(node: MetaNode, overrides: ExecutionOverrides) -> None:
    """Apply a meta-directive to the overrides object."""
    if node.key == MetaKey.MODEL:
        overrides.model = node.value
    elif node.key == MetaKey.SCOPE:
        overrides.scope = node.value
    elif node.key == MetaKey.STRATEGY:
        overrides.strategy = node.value
    elif node.key == MetaKey.BUDGET:
        try:
            overrides.budget = int(node.value)
        except ValueError:
            logger.warning("Invalid budget value: %s", node.value)


def _build_context_blocks(resolved: list[ResolvedDirective]) -> list[ContextBlock]:
    """Build context blocks from resolved directives (excluding text and meta)."""
    blocks: list[ContextBlock] = []
    for r in resolved:
        if r.content and not r.error and not isinstance(r.node, (TextNode, MetaNode)):
            blocks.append(
                ContextBlock(
                    source=r.source,
                    content=r.content,
                    relevance=r.relevance,
                    directive_type=r.node.node_type,
                )
            )
    # Sort by relevance (highest first)
    blocks.sort(key=lambda b: b.relevance, reverse=True)
    return blocks
