# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tier 3: Multi-agent orchestration — workflows with real LLMs.

Scenarios 12-16:
12. SequentialAgent pipeline
13. ParallelAgent concurrent research
14. LoopAgent iterative refinement
15. Agent-as-tool orchestrator
16. Durable workflow checkpoint + resume
"""

from __future__ import annotations

import pytest

from sagewai.core.agent_tool import agent_as_tool
from sagewai.core.workflows import LoopAgent, ParallelAgent, SequentialAgent
from sagewai.engines.universal import UniversalAgent

# --- Scenario 12: Sequential pipeline ---


@pytest.mark.integration
async def test_sequential_pipeline():
    """Three agents in sequence: research -> draft -> edit."""
    researcher = UniversalAgent(
        name="researcher",
        model="claude-haiku-4-5-20251001",
        system_prompt="Provide 3 key facts about the topic. Be concise.",
    )
    drafter = UniversalAgent(
        name="drafter",
        model="claude-haiku-4-5-20251001",
        system_prompt="Using the research provided, write a brief 2-sentence summary.",
    )
    editor = UniversalAgent(
        name="editor",
        model="claude-haiku-4-5-20251001",
        system_prompt="Polish and improve the text. Keep it concise.",
    )

    pipeline = SequentialAgent(
        name="content-pipeline",
        agents=[researcher, drafter, editor],
    )
    response = await pipeline.chat("Artificial Intelligence in healthcare")
    assert len(response) > 20, f"Pipeline output too short: {response!r}"


# --- Scenario 13: Parallel research ---


@pytest.mark.integration
async def test_parallel_research():
    """Three agents research concurrently, results merged."""
    agents = [
        UniversalAgent(
            name=f"researcher-{topic}",
            model="claude-haiku-4-5-20251001",
            system_prompt=f"You research {topic}. Give 2 key points in 1-2 sentences.",
        )
        for topic in ["benefits", "risks", "timeline"]
    ]

    parallel = ParallelAgent(name="parallel-research", agents=agents)
    response = await parallel.chat("AI adoption in enterprises")
    assert len(response) > 50, f"Parallel output too short: {response!r}"


# --- Scenario 14: Loop agent ---


@pytest.mark.integration
async def test_loop_refinement():
    """Agent iterates until output meets a quality threshold."""
    refiner = UniversalAgent(
        name="refiner",
        model="claude-haiku-4-5-20251001",
        system_prompt=(
            "Improve the given text to be more concise and impactful. "
            "If the text is already good, respond with exactly: DONE"
        ),
    )

    loop = LoopAgent(
        name="refinement-loop",
        agent=refiner,
        max_iterations=3,
        should_stop=lambda result, _i: "DONE" in result,
    )
    response = await loop.chat(
        "AI is a thing that is really good at doing stuff with computers and "
        "it can help people do things faster and better in many ways."
    )
    assert len(response) > 0


# --- Scenario 15: Agent-as-tool ---


@pytest.mark.integration
async def test_agent_as_tool():
    """Orchestrator delegates to specialist via agent-as-tool."""
    specialist = UniversalAgent(
        name="fact-checker",
        model="claude-haiku-4-5-20251001",
        system_prompt="You verify facts. State if a claim is true or false with a brief explanation.",
    )
    specialist_tool = agent_as_tool(specialist, "Verify a factual claim")

    orchestrator = UniversalAgent(
        name="orchestrator",
        model="claude-haiku-4-5-20251001",
        tools=[specialist_tool],
        system_prompt="Use the fact-checker tool to verify claims before answering.",
    )
    response = await orchestrator.chat("Is it true that Python was created in 1991?")
    assert len(response) > 10


# --- Scenario 16: Durable workflow (requires PostgreSQL) ---


@pytest.mark.integration
async def test_durable_workflow_checkpoint(database_url: str):
    """Workflow checkpoints to PostgreSQL and can resume."""
    from sagewai.core.stores.postgres import PostgresStore

    # Strip SQLAlchemy dialect suffix — asyncpg needs plain postgresql:// DSN
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    store = PostgresStore(dsn)
    await store.initialize()

    try:
        step1 = UniversalAgent(
            name="step1-agent",
            model="claude-haiku-4-5-20251001",
            system_prompt="Summarize the topic in one sentence.",
        )
        step2 = UniversalAgent(
            name="step2-agent",
            model="claude-haiku-4-5-20251001",
            system_prompt="Expand the summary into 3 bullet points.",
        )

        pipeline = SequentialAgent(
            name="durable-pipeline",
            agents=[step1, step2],
            workflow_store=store,
        )
        response = await pipeline.chat("Benefits of test-driven development")
        assert len(response) > 20
    finally:
        await store.close()
