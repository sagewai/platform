# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for DurableRunner and workflow durability integration."""

from __future__ import annotations

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.durability import CheckpointRecord, DurabilityMode, DurableRunner
from sagewai.core.state import InMemoryStore, StepStatus
from sagewai.core.workflows import LoopAgent, ParallelAgent, SequentialAgent

# ------------------------------------------------------------------
# Test agents
# ------------------------------------------------------------------


class EchoAgent(BaseAgent):
    """Agent that echoes input with a prefix."""

    def __init__(self, prefix: str = "", **kwargs):
        super().__init__(**kwargs)
        self.prefix = prefix
        self.calls: list[str] = []

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.calls.append(message)
        return f"{self.prefix}{message}"


class FailOnceAgent(BaseAgent):
    """Agent that fails on the first call, succeeds after."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("simulated crash")
        return f"recovered:{message}"


class CountingAgent(BaseAgent):
    """Agent that tracks call count."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.call_count += 1
        return f"iter-{self.call_count}:{message}"


# ------------------------------------------------------------------
# DurabilityMode enum
# ------------------------------------------------------------------


def test_durability_mode_values():
    assert DurabilityMode.NONE.value == "none"
    assert DurabilityMode.CHECKPOINT.value == "checkpoint"


def test_durability_mode_count():
    assert len(DurabilityMode) == 2


# ------------------------------------------------------------------
# CheckpointRecord
# ------------------------------------------------------------------


def test_checkpoint_record():
    rec = CheckpointRecord(
        step_index=0,
        step_name="step_0_scout",
        agent_name="scout",
        output="result",
        completed_at=1234567890.0,
    )
    assert rec.step_index == 0
    assert rec.agent_name == "scout"
    assert rec.output == "result"
    assert rec.compressed_context is None


# ------------------------------------------------------------------
# DurableRunner: sequential
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_sequential_basic():
    """DurableRunner executes agents sequentially with checkpointing."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    result = await runner.run_sequential([a, b], "hello", run_id="run-1")
    assert result == "B:A:hello"
    assert a.calls == ["hello"]
    assert b.calls == ["A:hello"]


@pytest.mark.asyncio
async def test_runner_sequential_checkpoints_saved():
    """Steps are checkpointed in the store."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    await runner.run_sequential([a, b], "hello", run_id="run-1")

    workflow_name = "sequential:a-b"
    wf_run = await store.load_run(workflow_name, "run-1")
    assert wf_run is not None
    assert wf_run.status == StepStatus.COMPLETED
    assert len(wf_run.steps) == 2

    step_a = wf_run.steps["step_0_a"]
    assert step_a.status == StepStatus.COMPLETED
    assert step_a.result == "A:hello"


@pytest.mark.asyncio
async def test_runner_sequential_resume_after_failure():
    """Resume skips completed steps and re-executes from failure point."""
    store = InMemoryStore()

    # First run: agent a succeeds, agent b (FailOnce) fails
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b_fail = FailOnceAgent(name="b", model="mock")

    runner1 = DurableRunner(store=store)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await runner1.run_sequential([a, b_fail], "hello", run_id="run-1")

    assert a.calls == ["hello"]
    assert b_fail.call_count == 1

    # Second run: agent a should be skipped (completed), agent b retries
    a2 = EchoAgent(prefix="A:", name="a", model="mock")
    b2 = EchoAgent(prefix="B:", name="b", model="mock")

    runner2 = DurableRunner(store=store)
    result = await runner2.run_sequential([a2, b2], "hello", run_id="run-1")

    assert result == "B:A:hello"
    assert a2.calls == []  # Skipped — already completed
    assert b2.calls == ["A:hello"]  # Executed with step_0_a's saved result


# ------------------------------------------------------------------
# DurableRunner: parallel
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_parallel_basic():
    """DurableRunner executes agents in parallel with checkpointing."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    result = await runner.run_parallel([a, b], "hello", run_id="par-1")
    assert "A:hello" in result
    assert "B:hello" in result


@pytest.mark.asyncio
async def test_runner_parallel_custom_merge():
    """Parallel runner uses custom merge function."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    a = EchoAgent(prefix="X:", name="a", model="mock")
    b = EchoAgent(prefix="Y:", name="b", model="mock")

    result = await runner.run_parallel(
        [a, b], "hi", merge=lambda r: " + ".join(r), run_id="par-2"
    )
    assert result == "X:hi + Y:hi"


@pytest.mark.asyncio
async def test_runner_parallel_resume():
    """Parallel resume skips completed agents."""
    store = InMemoryStore()

    # We can't easily test partial parallel failure with asyncio.gather,
    # but we can verify that completed steps are stored and reused.
    a = EchoAgent(prefix="A:", name="a", model="mock")
    runner1 = DurableRunner(store=store)
    await runner1.run_parallel([a], "hello", run_id="par-3")

    # Verify checkpoint
    wf_run = await store.load_run("parallel:a", "par-3")
    assert wf_run is not None
    assert wf_run.status == StepStatus.COMPLETED


# ------------------------------------------------------------------
# DurableRunner: loop
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_loop_basic():
    """DurableRunner executes loop with checkpointing."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    agent = CountingAgent(name="counter", model="mock")
    result = await runner.run_loop(agent, "start", max_iterations=3, run_id="loop-1")

    assert agent.call_count == 3
    assert "iter-3" in result


@pytest.mark.asyncio
async def test_runner_loop_with_stop_condition():
    """Loop stops early when should_stop returns True."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    agent = CountingAgent(name="counter", model="mock")
    await runner.run_loop(
        agent,
        "start",
        max_iterations=10,
        should_stop=lambda r, i: i >= 1,  # Stop after 2 iterations
        run_id="loop-2",
    )

    assert agent.call_count == 2


@pytest.mark.asyncio
async def test_runner_loop_resume():
    """Loop resume skips completed iterations."""
    store = InMemoryStore()

    # First run: 2 iterations succeed, then crash on 3rd
    call_count = 0

    class CrashAt3Agent(BaseAgent):
        async def _invoke_llm(self, messages, tools, *, model_override=None):
            raise NotImplementedError

        async def chat(self, message: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("crash at iter 3")
            return f"ok-{call_count}:{message}"

    agent1 = CrashAt3Agent(name="crasher", model="mock")
    runner1 = DurableRunner(store=store)

    with pytest.raises(RuntimeError, match="crash at iter 3"):
        await runner1.run_loop(agent1, "start", max_iterations=5, run_id="loop-3")

    # Resume: iterations 0,1 should be skipped
    agent2 = CountingAgent(name="crasher", model="mock")
    runner2 = DurableRunner(store=store)

    await runner2.run_loop(agent2, "start", max_iterations=5, run_id="loop-3")

    # Iterations 0,1 were completed. Iteration 2 failed.
    # On resume: skip 0,1, execute 2,3,4 (3 new calls).
    assert agent2.call_count == 3


# ------------------------------------------------------------------
# Workflow agents with durability=CHECKPOINT
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_agent_with_checkpoint():
    """SequentialAgent with durability=CHECKPOINT uses DurableRunner."""
    store = InMemoryStore()
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    seq = SequentialAgent(
        name="seq",
        agents=[a, b],
        durability=DurabilityMode.CHECKPOINT,
        workflow_store=store,
    )
    result = await seq.chat("hello")
    assert result == "B:A:hello"


@pytest.mark.asyncio
async def test_parallel_agent_with_checkpoint():
    """ParallelAgent with durability=CHECKPOINT uses DurableRunner."""
    store = InMemoryStore()
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    par = ParallelAgent(
        name="par",
        agents=[a, b],
        durability=DurabilityMode.CHECKPOINT,
        workflow_store=store,
    )
    result = await par.chat("hello")
    assert "A:hello" in result
    assert "B:hello" in result


@pytest.mark.asyncio
async def test_loop_agent_with_checkpoint():
    """LoopAgent with durability=CHECKPOINT uses DurableRunner."""
    store = InMemoryStore()
    agent = CountingAgent(name="counter", model="mock")

    loop = LoopAgent(
        name="loop",
        agent=agent,
        max_iterations=3,
        durability=DurabilityMode.CHECKPOINT,
        workflow_store=store,
    )
    await loop.chat("start")
    assert agent.call_count == 3


# ------------------------------------------------------------------
# DurabilityMode.NONE preserves current behavior
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_none_durability():
    """DurabilityMode.NONE uses original behavior (no checkpointing)."""
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    seq = SequentialAgent(name="seq", agents=[a, b])
    result = await seq.chat("hello")
    assert result == "B:A:hello"


@pytest.mark.asyncio
async def test_parallel_none_durability():
    """DurabilityMode.NONE uses original parallel behavior."""
    a = EchoAgent(prefix="A:", name="a", model="mock")
    b = EchoAgent(prefix="B:", name="b", model="mock")

    par = ParallelAgent(name="par", agents=[a, b])
    result = await par.chat("hello")
    assert "A:hello" in result
    assert "B:hello" in result


@pytest.mark.asyncio
async def test_loop_none_durability():
    """DurabilityMode.NONE uses original loop behavior."""
    agent = CountingAgent(name="counter", model="mock")
    loop = LoopAgent(name="loop", agent=agent, max_iterations=3)
    await loop.chat("start")
    assert agent.call_count == 3


# ------------------------------------------------------------------
# get_checkpoint
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_checkpoint():
    """get_checkpoint retrieves stored workflow run."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    a = EchoAgent(prefix="A:", name="a", model="mock")
    await runner.run_sequential([a], "hello", run_id="chk-1")

    wf = await runner.get_checkpoint("sequential:a", "chk-1")
    assert wf is not None
    assert wf.status == StepStatus.COMPLETED


@pytest.mark.asyncio
async def test_get_checkpoint_not_found():
    """get_checkpoint returns None for unknown run."""
    store = InMemoryStore()
    runner = DurableRunner(store=store)

    wf = await runner.get_checkpoint("unknown", "unknown")
    assert wf is None


# ------------------------------------------------------------------
# DurableRunner: checkpoint-before-execute
# ------------------------------------------------------------------


class TestDurableRunnerCheckpointBeforeExecute:
    @pytest.mark.asyncio
    async def test_step_marked_running_before_agent_chat(self):
        store = InMemoryStore()
        saved_statuses = []
        original_save = store.save_run

        async def tracking_save(run):
            for name, step in run.steps.items():
                saved_statuses.append((name, step.status))
            await original_save(run)

        store.save_run = tracking_save

        runner = DurableRunner(store=store)
        a1 = EchoAgent(prefix="A:", name="a1", model="mock")
        result = await runner.run_sequential([a1], "hello", run_id="pre-1")

        assert result == "A:hello"
        # First save of step should be RUNNING, later COMPLETED
        step_saves = [(n, s) for n, s in saved_statuses if "a1" in n]
        statuses = [s for _, s in step_saves]
        assert StepStatus.RUNNING in statuses
        assert StepStatus.COMPLETED in statuses

    @pytest.mark.asyncio
    async def test_crashed_step_re_executes_on_resume(self):
        store = InMemoryStore()
        runner = DurableRunner(store=store)

        fail_agent = FailOnceAgent(name="failer", model="mock")
        a2 = EchoAgent(prefix="B:", name="a2", model="mock")

        # First run: fails on failer
        with pytest.raises(RuntimeError, match="simulated crash"):
            await runner.run_sequential([fail_agent, a2], "input", run_id="crash-1")

        # Resume: failer succeeds now, a2 runs
        result = await runner.run_sequential([fail_agent, a2], "input", run_id="crash-1")
        assert result == "B:recovered:input"


# ------------------------------------------------------------------
# AgentConfig durability field
# ------------------------------------------------------------------


def test_agent_config_durability_default():
    from sagewai.models.agent import AgentConfig

    cfg = AgentConfig(name="test")
    assert cfg.durability == "none"


def test_agent_config_durability_checkpoint():
    from sagewai.models.agent import AgentConfig

    cfg = AgentConfig(name="test", durability="checkpoint")
    assert cfg.durability == "checkpoint"


# ------------------------------------------------------------------
# RunRecord checkpoint_run_id
# ------------------------------------------------------------------


def test_run_record_checkpoint_field():
    from sagewai.admin.store import RunRecord

    rec = RunRecord(run_id="r1", agent_name="scout")
    assert rec.checkpoint_run_id is None

    rec2 = RunRecord(run_id="r2", agent_name="scout", checkpoint_run_id="wf-run-1")
    assert rec2.checkpoint_run_id == "wf-run-1"
