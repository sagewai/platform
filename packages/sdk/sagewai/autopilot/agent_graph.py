# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Agent-graph state machine for autopilot blueprints.

An :class:`AgentGraph` is a tiny state machine: typed :class:`Agent`
nodes connected by plain edges (``(src, dst)``) or conditional
:class:`Branch` edges whose target depends on a string expression.

The state-machine model is chosen deliberately over a pure DAG because
the batch reference blueprint (``document-intake-extract``) needs
conditional routing based on a validator's confidence. Linear graphs
without any branches are the common case and are just "state machines
with only unconditional edges"; this keeps the framework one concept,
not two.

Note: this module defines the graph *shape* and traversal invariants.
It does not execute agents — execution is the job of
``sagewai.autopilot.mission`` plus (later) the ``AutopilotController``.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._types import AgentKind
from .errors import AgentGraphError

CondEval = Callable[[str, dict[str, object]], bool]


class Agent(BaseModel):
    """A node in an :class:`AgentGraph`."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    kind: AgentKind
    role: str | None = None
    prompt_ref: str | None = None
    tools: tuple[str, ...] = ()
    output_schema_ref: str | None = None
    max_steps: int = Field(default=1, ge=1)
    deterministic_fallback: bool = False

    @model_validator(mode="after")
    def _llm_needs_prompt_ref(self) -> Agent:
        if self.kind is AgentKind.LLM and self.prompt_ref is None:
            raise ValueError("LLM agents must set prompt_ref")
        return self


class Branch(BaseModel):
    """A conditional edge out of a node.

    Conditions are strings interpreted by the mission runtime (a later
    sub-plan). This module only stores them — it does not evaluate them.
    """

    model_config = ConfigDict(frozen=True)

    condition: str = Field(min_length=1)
    target: str = Field(min_length=1)


Edge = tuple[str, str]


class AgentGraph(BaseModel):
    """A state machine of :class:`Agent` nodes plus unconditional edges
    and conditional :class:`Branch` outs.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    nodes: tuple[Agent, ...]
    edges: tuple[Edge, ...] = ()
    branches: dict[str, tuple[Branch, ...]] = Field(default_factory=dict)
    entry: str

    # ── Invariants ────────────────────────────────────────────────

    @field_validator("nodes")
    @classmethod
    def _nodes_not_empty(cls, v: tuple[Agent, ...]) -> tuple[Agent, ...]:
        if len(v) == 0:
            raise ValueError("agent graph must have at least one node")
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> AgentGraph:
        ids = [n.id for n in self.nodes]
        seen: set[str] = set()
        for nid in ids:
            if nid in seen:
                raise AgentGraphError(f"duplicate node id {nid!r}", node_id=nid)
            seen.add(nid)

        if self.entry not in seen:
            raise AgentGraphError(f"entry {self.entry!r} is not a declared node")

        for src, dst in self.edges:
            if src not in seen:
                raise AgentGraphError("unknown node in edge src", node_id=src)
            if dst not in seen:
                raise AgentGraphError("unknown node in edge dst", node_id=dst)

        for src, branches in self.branches.items():
            if src not in seen:
                raise AgentGraphError("unknown node in branches", node_id=src)
            for br in branches:
                if br.target not in seen:
                    raise AgentGraphError(
                        f"branch from {src!r} points to unknown {br.target!r}",
                        node_id=src,
                    )

        # Unconditional edges must form a DAG. Branches are allowed to
        # loop; bounded at runtime by Agent.max_steps.
        self._reject_unconditional_cycles()
        return self

    def _reject_unconditional_cycles(self) -> None:
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for src, dst in self.edges:
            adj[src].append(dst)

        white, gray, black = 0, 1, 2
        color: dict[str, int] = {nid: white for nid in adj}

        def dfs(u: str) -> None:
            color[u] = gray
            for v in adj[u]:
                if color[v] == gray:
                    raise AgentGraphError(
                        f"cycle detected through {u!r} -> {v!r}",
                        node_id=u,
                    )
                if color[v] == white:
                    dfs(v)
            color[u] = black

        for nid in adj:
            if color[nid] == white:
                dfs(nid)

    # ── Traversal ────────────────────────────────────────────────

    def traverse_linear(self) -> list[str]:
        """Return node ids in edge order, starting at :attr:`entry`.

        Only valid for graphs with no branches. Raises
        :class:`AgentGraphError` otherwise.
        """
        if self.branches:
            raise AgentGraphError("traverse_linear: graph has branches; use resolve_next in a loop")

        adj: dict[str, str | None] = {n.id: None for n in self.nodes}
        for src, dst in self.edges:
            if adj[src] is not None:
                raise AgentGraphError(
                    f"node {src!r} has multiple unconditional successors; "
                    "use branches for conditional routing",
                    node_id=src,
                )
            adj[src] = dst

        order: list[str] = []
        current: str | None = self.entry
        seen: set[str] = set()
        while current is not None:
            if current in seen:  # defensive — cycle check should have caught
                raise AgentGraphError("cycle in traversal", node_id=current)
            seen.add(current)
            order.append(current)
            current = adj[current]
        return order

    def resolve_next(
        self,
        node_id: str,
        *,
        context: dict[str, object],
        cond_eval: CondEval | None = None,
    ) -> str | None:
        """Return the next node id after ``node_id``, or ``None`` if terminal.

        If ``node_id`` has entries in :attr:`branches`, the supplied
        ``cond_eval(expression, context) -> bool`` callable is required
        to pick the first branch whose condition evaluates truthy.
        Otherwise, looks up the first unconditional edge out of
        ``node_id``.

        ``None`` is returned in two distinct situations:
        (a) the node has no outgoing edges or branches (truly terminal), or
        (b) the node has branches but none of their conditions evaluated
        truthy. The mission runtime is expected to treat (b) as a logic
        error in the blueprint; this module does not raise for it so
        that callers can handle the ambiguity in their own way.
        """
        branches = self.branches.get(node_id)
        if branches:
            if cond_eval is None:
                raise AgentGraphError(
                    f"node {node_id!r} has branches but no cond_eval supplied",
                    node_id=node_id,
                )
            for br in branches:
                if cond_eval(br.condition, context):
                    return br.target
            return None

        for src, dst in self.edges:
            if src == node_id:
                return dst
        return None
