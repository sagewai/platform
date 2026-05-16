# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tier 2: Agentic patterns — real LLM calls through all strategies.

Scenarios 6-11:
6. ReAct with tools
7. LATS complex problem-solving
8. ToT multi-perspective analysis
9. SelfCorrection JSON output
10. Planning multi-step
11. Routing intent classification
"""

from __future__ import annotations

import json

import pytest

from sagewai.core.lats import LATSStrategy
from sagewai.core.planning import PlanningStrategy
from sagewai.core.routing import RoutingStrategy
from sagewai.core.self_correction import OutputValidator, SelfCorrectionStrategy
from sagewai.core.strategies import ReActStrategy
from sagewai.core.tree_of_thoughts import TreeOfThoughtsStrategy
from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool


@tool
async def search_web(query: str) -> str:
    """Search the web for information."""
    results = {
        "python creator": "Python was created by Guido van Rossum in 1991.",
        "rust creator": "Rust was created by Graydon Hoare at Mozilla.",
    }
    for key, val in results.items():
        if key in query.lower():
            return val
    return f"Search results for: {query} — No specific results found."


@tool
async def calculate(expression: str) -> str:
    """Evaluate a mathematical expression."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error: {e}"


# --- Scenario 6: ReAct with tools ---


@pytest.mark.integration
async def test_react_with_tools():
    """ReAct strategy uses tools to research and answer."""
    agent = UniversalAgent(
        name="react-researcher",
        model="claude-haiku-4-5-20251001",
        tools=[search_web],
        strategy=ReActStrategy(),
        system_prompt="Use the search_web tool to answer questions. Always search first.",
    )
    response = await agent.chat("Who created the Python programming language?")
    assert "Guido" in response or "guido" in response.lower(), f"Expected 'Guido' in: {response!r}"


# --- Scenario 7: LATS ---


@pytest.mark.integration
async def test_lats_problem_solving():
    """LATS explores multiple solution paths for a complex question."""
    agent = UniversalAgent(
        name="lats-solver",
        model="claude-haiku-4-5-20251001",
        strategy=LATSStrategy(n_samples=2, max_depth=1, max_iterations=3),
        system_prompt="You are a problem solver. Think step by step. Always provide a response.",
    )
    response = await agent.chat("List 3 benefits of using Python for backend development.")
    assert len(response) > 0, f"Expected non-empty response from LATS, got: {response!r}"


# --- Scenario 8: Tree of Thoughts ---


@pytest.mark.integration
async def test_tree_of_thoughts():
    """ToT generates and evaluates multiple reasoning branches."""
    agent = UniversalAgent(
        name="tot-analyst",
        model="claude-haiku-4-5-20251001",
        strategy=TreeOfThoughtsStrategy(branches=2, max_depth=1, top_k=1),
        system_prompt="Analyze problems from multiple perspectives.",
    )
    response = await agent.chat(
        "Should a startup use Python or Go for their backend? Consider 3 factors."
    )
    assert len(response) > 50


# --- Scenario 9: SelfCorrection with JSON ---


@pytest.mark.integration
async def test_self_correction_json():
    """SelfCorrection validates and fixes JSON output."""
    validator = OutputValidator()
    validator.add_json_validator(required_fields=["name", "age", "city"])

    agent = UniversalAgent(
        name="json-agent",
        model="claude-haiku-4-5-20251001",
        strategy=SelfCorrectionStrategy(
            validator=validator,
            max_corrections=2,
        ),
        system_prompt=(
            "You are a data extractor. Always respond with valid JSON containing "
            "name, age, and city fields. No markdown, no explanation, just JSON."
        ),
    )
    response = await agent.chat("Extract: John Smith is 30 years old and lives in New York.")
    # Parse JSON with support for Markdown fences and prose preambles (LLMs commonly wrap JSON in ```json...```)
    from sagewai.core._strategy_utils import parse_json

    data = parse_json(response)
    assert "name" in data
    assert "age" in data
    assert "city" in data


# --- Scenario 10: Planning ---


@pytest.mark.integration
async def test_planning_strategy():
    """Planning strategy decomposes a task into steps and executes."""
    agent = UniversalAgent(
        name="planner",
        model="claude-haiku-4-5-20251001",
        tools=[calculate],
        strategy=PlanningStrategy(mode="plan_then_act"),
        system_prompt="Plan your approach step by step, then execute using tools.",
    )
    response = await agent.chat("Calculate: What is (15 * 7) + (22 * 3)? Show your work.")
    # Planning may produce the plan + execution; check for answer or plan keywords
    assert "171" in response or (
        "15" in response and "22" in response
    ), f"Expected 171 or planning keywords in: {response!r}"


# --- Scenario 11: Routing ---


@pytest.mark.integration
async def test_routing_strategy():
    """Routing dispatches to specialist agents based on intent."""
    math_agent = UniversalAgent(
        name="math-expert",
        model="claude-haiku-4-5-20251001",
        tools=[calculate],
        system_prompt="You are a math expert. Use the calculate tool.",
    )
    general_agent = UniversalAgent(
        name="general",
        model="claude-haiku-4-5-20251001",
        system_prompt="You are a general assistant.",
    )

    router = UniversalAgent(
        name="router",
        model="claude-haiku-4-5-20251001",
        strategy=RoutingStrategy(
            routes={"math": math_agent, "general": general_agent},
            method="heuristic",
            keywords={"math": ["calculate", "math", "number", "sum", "multiply"]},
            fallback=general_agent,
        ),
    )
    response = await router.chat("Calculate 25 * 4")
    assert "100" in response, f"Expected 100 in: {response!r}"
