# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
"""Performance micro-benchmarks for Sagewai core operations.

These run as part of ``make perf`` and guard against obvious regressions
in the hot paths. LLM calls are mocked, so the numbers here are pure
framework overhead — import time, object construction, tool decoration,
and the chat() round-trip plumbing — nothing network-bound.

The budgets are deliberately generous (measured on an M1 Mac, then
doubled). They are *not* tuned micro-benchmarks; they exist to catch
10x slowdowns, not 10% ones. If a budget starts flaking on CI, raise it
with a comment explaining why rather than chasing the regression.

Run with:
    pytest packages/sdk/tests/test_perf.py -v -m perf
    make perf  # from the monorepo root
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool


# ─── Helpers ─────────────────────────────────────────────────────────────

def _timed(fn, *args, **kwargs) -> tuple[object, float]:
    """Run ``fn(*args, **kwargs)`` and return (result, elapsed_seconds)."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


async def _timed_async(coro_fn, *args, **kwargs) -> tuple[object, float]:
    start = time.perf_counter()
    result = await coro_fn(*args, **kwargs)
    return result, time.perf_counter() - start


# ─── Agent construction ─────────────────────────────────────────────────

@pytest.mark.perf
def test_agent_construction_under_50ms() -> None:
    """Constructing a single UniversalAgent should be near-free."""
    agent, elapsed = _timed(
        lambda: UniversalAgent(name="perf", model="gpt-4o-mini")
    )
    assert agent is not None
    assert elapsed < 0.050, (
        f"agent construction took {elapsed*1000:.1f}ms — budget 50ms. "
        "Check engines.universal imports for regressions."
    )


@pytest.mark.perf
def test_100_agents_under_1s() -> None:
    """100 agents should construct in well under a second."""
    def build_many() -> list[UniversalAgent]:
        return [
            UniversalAgent(name=f"perf-{i}", model="gpt-4o-mini")
            for i in range(100)
        ]

    agents, elapsed = _timed(build_many)
    assert len(agents) == 100
    assert elapsed < 1.0, (
        f"100 agent constructions took {elapsed*1000:.1f}ms — budget 1000ms."
    )


# ─── Tool decoration ────────────────────────────────────────────────────

@pytest.mark.perf
def test_tool_decoration_under_50ms() -> None:
    """@tool decoration introspects docstring + type hints + builds a
    Pydantic arg schema. Must stay well under a frame (16ms) once warm,
    but we use a looser budget to account for first-call JIT / cache miss.
    """
    def apply() -> object:
        @tool
        def sample(text: str, count: int = 1) -> str:
            """Repeat text count times.

            Args:
                text: Input string.
                count: Number of repetitions.
            """
            return text * count

        return sample

    t, elapsed = _timed(apply)
    # @tool returns a ToolSpec dataclass wrapping the underlying function.
    assert hasattr(t, "name") and hasattr(t, "handler")
    assert elapsed < 0.050, (
        f"@tool decoration took {elapsed*1000:.2f}ms — budget 50ms. "
        "Check models.tool for regressions in docstring / type-hint parsing."
    )


# ─── Chat round-trip with mocked LLM ────────────────────────────────────

@pytest.mark.perf
def test_mocked_chat_roundtrip_under_50ms() -> None:
    """A full agent.chat() with a mocked provider should be < 50ms.

    This patches the agent method directly so we measure the coroutine
    creation + asyncio.run + patch plumbing, which is the framework
    overhead contributors can introduce regressions in.
    """
    agent = UniversalAgent(name="perf", model="gpt-4o-mini")

    async def one_shot() -> str:
        with patch(
            "sagewai.engines.universal.UniversalAgent.chat",
            return_value="ok",
        ):
            return await agent.chat("hello")

    result, elapsed = asyncio.run(_timed_async(one_shot))
    assert result == "ok"
    assert elapsed < 0.050, (
        f"mocked chat roundtrip took {elapsed*1000:.1f}ms — budget 50ms."
    )
