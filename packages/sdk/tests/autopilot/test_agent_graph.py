# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the AgentGraph state machine."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.autopilot._types import AgentKind
from sagewai.autopilot.agent_graph import Agent, AgentGraph, Branch
from sagewai.autopilot.errors import AgentGraphError

# ── Phase 1: Agent construction ───────────────────────────────────


def test_agent_llm_kind_has_prompt_ref():
    a = Agent(id="scout", kind=AgentKind.LLM, prompt_ref="prompts/scout.md")
    assert a.kind is AgentKind.LLM
    assert a.prompt_ref == "prompts/scout.md"


def test_agent_deterministic_kind_does_not_require_prompt_ref():
    a = Agent(id="validator", kind=AgentKind.DETERMINISTIC)
    assert a.prompt_ref is None


def test_agent_llm_kind_requires_prompt_ref():
    with pytest.raises(ValidationError, match="prompt_ref"):
        Agent(id="scout", kind=AgentKind.LLM)


def test_agent_id_must_be_non_empty_identifier():
    with pytest.raises(ValidationError):
        Agent(id="", kind=AgentKind.DETERMINISTIC)


# ── Phase 2: Graph construction and invariants ────────────────────


def _linear_graph() -> AgentGraph:
    return AgentGraph(
        nodes=(
            Agent(id="scout", kind=AgentKind.LLM, prompt_ref="p/s.md"),
            Agent(id="curator", kind=AgentKind.LLM, prompt_ref="p/c.md"),
            Agent(id="summarizer", kind=AgentKind.LLM, prompt_ref="p/sm.md"),
        ),
        edges=(("scout", "curator"), ("curator", "summarizer")),
        entry="scout",
    )


def test_agent_graph_constructs_from_linear_spec():
    g = _linear_graph()
    assert g.entry == "scout"
    assert len(g.nodes) == 3
    assert len(g.edges) == 2


def test_agent_graph_rejects_unknown_entry_node():
    with pytest.raises(AgentGraphError, match="entry"):
        AgentGraph(
            nodes=(Agent(id="a", kind=AgentKind.DETERMINISTIC),),
            edges=(),
            entry="nope",
        )


def test_agent_graph_rejects_edge_to_unknown_node():
    with pytest.raises(AgentGraphError, match="unknown node"):
        AgentGraph(
            nodes=(Agent(id="a", kind=AgentKind.DETERMINISTIC),),
            edges=(("a", "b"),),
            entry="a",
        )


def test_agent_graph_rejects_duplicate_node_ids():
    with pytest.raises(AgentGraphError, match="duplicate"):
        AgentGraph(
            nodes=(
                Agent(id="a", kind=AgentKind.DETERMINISTIC),
                Agent(id="a", kind=AgentKind.DETERMINISTIC),
            ),
            edges=(),
            entry="a",
        )


# ── Phase 3: Linear traversal ─────────────────────────────────────


def test_linear_traversal_visits_nodes_in_edge_order():
    g = _linear_graph()
    order = list(g.traverse_linear())
    assert order == ["scout", "curator", "summarizer"]


def test_traverse_linear_on_branched_graph_raises():
    g = AgentGraph(
        nodes=(
            Agent(id="a", kind=AgentKind.DETERMINISTIC),
            Agent(id="b", kind=AgentKind.DETERMINISTIC),
            Agent(id="c", kind=AgentKind.DETERMINISTIC),
        ),
        edges=(("a", "b"),),
        branches={
            "b": (
                Branch(condition="x > 0", target="a"),
                Branch(condition="x <= 0", target="c"),
            ),
        },
        entry="a",
    )
    with pytest.raises(AgentGraphError, match="branches"):
        list(g.traverse_linear())


# ── Phase 4: Branch resolution ────────────────────────────────────


def test_resolve_next_returns_unconditional_successor():
    g = _linear_graph()
    assert g.resolve_next("scout", context={}) == "curator"


def test_resolve_next_picks_branch_whose_condition_matches():
    def cond_eval(expr: str, ctx: dict) -> bool:
        # toy evaluator — mission runtime has the real one
        return eval(expr, {}, ctx)  # noqa: S307 — test only

    g = AgentGraph(
        nodes=(
            Agent(id="classifier", kind=AgentKind.LLM, prompt_ref="p.md"),
            Agent(id="router", kind=AgentKind.DETERMINISTIC),
            Agent(id="review", kind=AgentKind.DETERMINISTIC),
        ),
        edges=(("classifier", "router"),),
        branches={
            "router": (
                Branch(condition="confidence >= 0.7", target="review"),
                Branch(condition="confidence <  0.7", target="classifier"),
            ),
        },
        entry="classifier",
    )
    assert g.resolve_next("router", context={"confidence": 0.9}, cond_eval=cond_eval) == "review"
    assert (
        g.resolve_next("router", context={"confidence": 0.5}, cond_eval=cond_eval) == "classifier"
    )


def test_resolve_next_returns_none_for_terminal_node():
    g = _linear_graph()
    assert g.resolve_next("summarizer", context={}) is None


def test_resolve_next_returns_none_when_no_branch_condition_matches():
    # Pins the documented behavior: if a node has branches but none
    # evaluate truthy, resolve_next returns None (same as terminal).
    # The mission runtime treats this as a blueprint logic error.
    def cond_eval(expr: str, ctx: dict[str, object]) -> bool:
        return False  # nothing ever matches

    g = AgentGraph(
        nodes=(
            Agent(id="a", kind=AgentKind.DETERMINISTIC),
            Agent(id="b", kind=AgentKind.DETERMINISTIC),
        ),
        edges=(),
        branches={"a": (Branch(condition="never", target="b"),)},
        entry="a",
    )
    assert g.resolve_next("a", context={}, cond_eval=cond_eval) is None


def test_resolve_next_raises_when_branches_set_but_no_cond_eval():
    g = AgentGraph(
        nodes=(
            Agent(id="a", kind=AgentKind.DETERMINISTIC),
            Agent(id="b", kind=AgentKind.DETERMINISTIC),
        ),
        edges=(),
        branches={"a": (Branch(condition="True", target="b"),)},
        entry="a",
    )
    with pytest.raises(AgentGraphError, match="cond_eval"):
        g.resolve_next("a", context={})


# ── Phase 5: Cycle detection ──────────────────────────────────────


def test_cycle_in_unconditional_edges_is_rejected():
    with pytest.raises(AgentGraphError, match="cycle"):
        AgentGraph(
            nodes=(
                Agent(id="a", kind=AgentKind.DETERMINISTIC),
                Agent(id="b", kind=AgentKind.DETERMINISTIC),
            ),
            edges=(("a", "b"), ("b", "a")),
            entry="a",
        )


def test_branches_creating_a_loop_are_allowed():
    # Branches can loop (e.g. "retry on low confidence") — this is OK,
    # because the mission runtime bounds iterations via max_steps.
    g = AgentGraph(
        nodes=(
            Agent(id="a", kind=AgentKind.LLM, prompt_ref="p.md"),
            Agent(id="b", kind=AgentKind.DETERMINISTIC),
        ),
        edges=(("a", "b"),),
        branches={
            "b": (
                Branch(condition="retry", target="a"),
                Branch(condition="done", target="a"),  # terminal, same target
            ),
        },
        entry="a",
    )
    # no exception
    assert g.entry == "a"
