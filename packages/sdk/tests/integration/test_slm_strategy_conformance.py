# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SLM conformance tests — every strategy must produce a usable result on a cheap local model.

This is the moat claim: every execution strategy in the Sagewai SDK must work
reliably on a locally-hosted small language model, not just frontier APIs.

Opt-in via:
    SAGEWAI_SLM_CONFORMANCE=1

Requires a running Ollama instance with the ``llama3.2`` model pulled:
    ollama pull llama3.2

Skipped automatically when the flag is absent — all parametrized cases show as
SKIPPED in normal CI runs, which is intentional.
"""

from __future__ import annotations

import os

import pytest

from sagewai.core.chain_of_thought import ChainOfThoughtStrategy
from sagewai.core.debate import DebateStrategy
from sagewai.core.evaluator_optimizer import EvaluatorOptimizerStrategy
from sagewai.core.lats import LATSStrategy
from sagewai.core.majority_vote import MajorityVoteStrategy
from sagewai.core.planning import PlanThenActStrategy
from sagewai.core.reflexion import ReflexionStrategy
from sagewai.core.routing import RoutingStrategy
from sagewai.core.self_correction import SelfCorrectionStrategy
from sagewai.core.strategies import ReActStrategy
from sagewai.core.tree_of_thoughts import TreeOfThoughtsStrategy
from sagewai.engines.universal import UniversalAgent

pytestmark = [pytest.mark.soak]

_SLM_MODEL = "ollama/llama3.2:latest"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slm_enabled() -> bool:
    return os.environ.get("SAGEWAI_SLM_CONFORMANCE") == "1"


def _make_fallback_agent() -> UniversalAgent:
    """Minimal fallback agent for RoutingStrategy."""
    return UniversalAgent(
        name="fallback",
        model=_SLM_MODEL,
        system_prompt="You are a general-purpose assistant.",
    )


def _make_route_agent(name: str) -> UniversalAgent:
    return UniversalAgent(
        name=name,
        model=_SLM_MODEL,
        system_prompt=f"You are a specialist assistant for {name} questions.",
    )


# ---------------------------------------------------------------------------
# Strategy fixture list
# ---------------------------------------------------------------------------

_STRATEGY_CASES: list[tuple[str, object]] = [
    ("ReAct", ReActStrategy()),
    ("ChainOfThought", ChainOfThoughtStrategy()),
    ("TreeOfThoughts", TreeOfThoughtsStrategy(branches=2, max_depth=1)),
    ("LATS", LATSStrategy(n_samples=2, max_depth=2, max_iterations=2)),
    ("SelfCorrection", SelfCorrectionStrategy(max_corrections=1)),
    ("PlanThenAct", PlanThenActStrategy()),
    ("Reflexion", ReflexionStrategy(max_attempts=2)),
    ("EvaluatorOptimizer", EvaluatorOptimizerStrategy(max_revisions=1)),
    ("Debate", DebateStrategy(n_debaters=2, max_rounds=1)),
    ("MajorityVote", MajorityVoteStrategy(n_samples=2, aggregation="first")),
    (
        "Routing",
        RoutingStrategy(
            routes={
                "math": _make_route_agent("math"),
            },
            fallback=_make_fallback_agent(),
            method="heuristic",
            keywords={"math": ["plus", "minus", "multiply", "divide", "sum"]},
        ),
    ),
]

_STRATEGY_IDS = [name for name, _ in _STRATEGY_CASES]


# ---------------------------------------------------------------------------
# Conformance test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name,strategy", _STRATEGY_CASES, ids=_STRATEGY_IDS)
async def test_strategy_runs_on_slm(name: str, strategy: object) -> None:
    """Every strategy must produce a non-empty string on a local SLM.

    The test verifies the complete execution path: strategy.execute() →
    UniversalAgent → Ollama llama3.2. A strategy returning empty or raising
    is a real SLM-robustness finding.
    """
    if not _slm_enabled():
        pytest.skip(
            "SLM conformance test skipped. "
            "Set SAGEWAI_SLM_CONFORMANCE=1 with Ollama + llama3.2 to run."
        )

    agent = UniversalAgent(
        name=f"slm_conformance_{name.lower()}",
        model=_SLM_MODEL,
        strategy=strategy,  # type: ignore[arg-type]
        max_iterations=5,
    )

    result = await agent.chat("What is 17 plus 26? Answer concisely.")

    assert isinstance(result, str), (
        f"Strategy {name}: expected str result, got {type(result).__name__}"
    )
    assert result.strip(), (
        f"Strategy {name}: result was empty or whitespace-only"
    )
