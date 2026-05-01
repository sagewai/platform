# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for DurableWorkflow — step-level checkpointing and recovery."""

from __future__ import annotations

import pytest

from sagewai.core.state import (
    DurableWorkflow,
    InMemoryStore,
    StepRecord,
    StepStatus,
    WorkflowRun,
    WorkflowStepError,
    WorkflowWaiting,
    _generate_run_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FailNTimes:
    """Callable that fails N times then succeeds."""

    def __init__(self, n: int, result: str = "success") -> None:
        self._remaining = n
        self._result = result

    async def __call__(self, **kwargs) -> str:
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("transient failure")
        return self._result


# ---------------------------------------------------------------------------
# StepStatus
# ---------------------------------------------------------------------------


class TestStepStatus:
    def test_values(self):
        assert StepStatus.PENDING == "pending"
        assert StepStatus.RUNNING == "running"
        assert StepStatus.COMPLETED == "completed"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.RETRYING == "retrying"


# ---------------------------------------------------------------------------
# StepRecord
# ---------------------------------------------------------------------------


class TestStepRecord:
    def test_defaults(self):
        rec = StepRecord(step_name="test")
        assert rec.status == StepStatus.PENDING
        assert rec.result is None
        assert rec.attempts == 0


# ---------------------------------------------------------------------------
# WorkflowRun
# ---------------------------------------------------------------------------


class TestWorkflowRun:
    def test_to_dict(self):
        run = WorkflowRun(workflow_name="test", run_id="abc123")
        run.steps["step1"] = StepRecord(
            step_name="step1", status=StepStatus.COMPLETED, result="done", attempts=1
        )
        d = run.to_dict()
        assert d["workflow_name"] == "test"
        assert d["run_id"] == "abc123"
        assert "step1" in d["steps"]
        assert d["steps"]["step1"]["status"] == "completed"


# ---------------------------------------------------------------------------
# InMemoryStore
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    @pytest.mark.asyncio
    async def test_save_and_load(self):
        store = InMemoryStore()
        run = WorkflowRun(workflow_name="wf", run_id="r1")
        await store.save_run(run)
        loaded = await store.load_run("wf", "r1")
        assert loaded is not None
        assert loaded.run_id == "r1"

    @pytest.mark.asyncio
    async def test_load_not_found(self):
        store = InMemoryStore()
        assert await store.load_run("wf", "nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_runs(self):
        store = InMemoryStore()
        r1 = WorkflowRun(workflow_name="wf", run_id="r1", status=StepStatus.COMPLETED)
        r2 = WorkflowRun(workflow_name="wf", run_id="r2", status=StepStatus.FAILED)
        r3 = WorkflowRun(workflow_name="other", run_id="r3")
        await store.save_run(r1)
        await store.save_run(r2)
        await store.save_run(r3)

        all_wf = await store.list_runs("wf")
        assert len(all_wf) == 2

        completed = await store.list_runs("wf", status=StepStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].run_id == "r1"


# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------


class TestRunIdGeneration:
    def test_deterministic(self):
        id1 = _generate_run_id("wf", {"topic": "test"})
        id2 = _generate_run_id("wf", {"topic": "test"})
        assert id1 == id2

    def test_different_inputs(self):
        id1 = _generate_run_id("wf", {"topic": "a"})
        id2 = _generate_run_id("wf", {"topic": "b"})
        assert id1 != id2

    def test_length(self):
        rid = _generate_run_id("wf", {"x": 1})
        assert len(rid) == 16


# ---------------------------------------------------------------------------
# DurableWorkflow — basic execution
# ---------------------------------------------------------------------------


class TestDurableWorkflowBasic:
    @pytest.mark.asyncio
    async def test_single_step(self):
        wf = DurableWorkflow(name="test-wf")

        @wf.step("greet")
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await wf.run(name="World")
        assert result == "Hello, World!"

    @pytest.mark.asyncio
    async def test_multi_step_pipeline(self):
        wf = DurableWorkflow(name="pipeline")
        call_order = []

        @wf.step("step1")
        async def step1(topic: str) -> str:
            call_order.append("step1")
            return f"researched: {topic}"

        @wf.step("step2")
        async def step2(topic: str) -> str:
            call_order.append("step2")
            return f"drafted: {topic}"

        result = await wf.run(topic="AI")
        assert call_order == ["step1", "step2"]
        assert "drafted" in result

    @pytest.mark.asyncio
    async def test_step_names(self):
        wf = DurableWorkflow(name="test")

        @wf.step("a")
        async def a():
            pass

        @wf.step("b")
        async def b():
            pass

        assert wf.step_names == ["a", "b"]

    @pytest.mark.asyncio
    async def test_workflow_state_tracking(self):
        store = InMemoryStore()
        wf = DurableWorkflow(name="tracked", store=store)

        @wf.step("do_work")
        async def do_work(x: str) -> str:
            return f"done: {x}"

        result = await wf.run(x="test")
        assert result == "done: test"

        runs = await store.list_runs("tracked")
        assert len(runs) == 1
        assert runs[0].status == StepStatus.COMPLETED
        assert runs[0].output_data == "done: test"
        assert "do_work" in runs[0].steps
        assert runs[0].steps["do_work"].status == StepStatus.COMPLETED


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_succeeds(self):
        wf = DurableWorkflow(name="retry-wf")
        fail_once = FailNTimes(1, "recovered")

        @wf.step("flaky", retries=2, retry_delay=0.01)
        async def flaky(x: str) -> str:
            return await fail_once(x=x)

        result = await wf.run(x="test")
        assert result == "recovered"

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        wf = DurableWorkflow(name="fail-wf")
        fail_always = FailNTimes(100, "never")

        @wf.step("always_fail", retries=2, retry_delay=0.01)
        async def always_fail(x: str) -> str:
            return await fail_always(x=x)

        with pytest.raises(WorkflowStepError) as exc_info:
            await wf.run(x="test")

        assert exc_info.value.step_name == "always_fail"
        assert exc_info.value.workflow_name == "fail-wf"

    @pytest.mark.asyncio
    async def test_no_retries_fails_immediately(self):
        wf = DurableWorkflow(name="no-retry")

        @wf.step("fail_step")
        async def fail_step(x: str) -> str:
            raise ValueError("boom")

        with pytest.raises(WorkflowStepError):
            await wf.run(x="test")


# ---------------------------------------------------------------------------
# Checkpoint and recovery
# ---------------------------------------------------------------------------


class TestCheckpointRecovery:
    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self):
        store = InMemoryStore()
        call_log = []

        # First run: step1 succeeds, step2 fails
        wf1 = DurableWorkflow(name="resume-wf", store=store)

        @wf1.step("step1")
        async def step1_v1(topic: str) -> str:
            call_log.append("step1")
            return "step1_result"

        @wf1.step("step2")
        async def step2_v1(topic: str) -> str:
            call_log.append("step2_fail")
            raise RuntimeError("step2 failed")

        with pytest.raises(WorkflowStepError):
            await wf1.run(run_id="run-1", topic="test")

        assert "step1" in call_log
        assert "step2_fail" in call_log

        # Verify checkpoint was saved
        saved = await store.load_run("resume-wf", "run-1")
        assert saved is not None
        assert saved.steps["step1"].status == StepStatus.COMPLETED

        # Resume: step1 should be skipped, step2 re-executed
        call_log.clear()
        wf2 = DurableWorkflow(name="resume-wf", store=store)

        @wf2.step("step1")
        async def step1_v2(topic: str) -> str:
            call_log.append("step1_resumed")
            return "should_not_run"

        @wf2.step("step2")
        async def step2_v2(topic: str) -> str:
            call_log.append("step2_resumed")
            return "final_result"

        result = await wf2.run(run_id="run-1", topic="test")
        assert result == "final_result"
        # step1 was skipped (already completed in checkpoint)
        assert "step1_resumed" not in call_log
        assert "step2_resumed" in call_log

    @pytest.mark.asyncio
    async def test_get_run(self):
        store = InMemoryStore()
        wf = DurableWorkflow(name="get-run", store=store)

        @wf.step("work")
        async def work(x: str) -> str:
            return "done"

        await wf.run(run_id="r1", x="test")
        run = await wf.get_run("r1")
        assert run is not None
        assert run.status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_list_runs(self):
        store = InMemoryStore()
        wf = DurableWorkflow(name="list-runs", store=store)

        @wf.step("work")
        async def work(x: str) -> str:
            return "done"

        await wf.run(run_id="r1", x="a")
        await wf.run(run_id="r2", x="b")

        runs = await wf.list_runs()
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_step_timeout(self):
        wf = DurableWorkflow(name="timeout-wf")

        @wf.step("slow", timeout=0.05)
        async def slow(x: str) -> str:
            import asyncio

            await asyncio.sleep(10)
            return "never"

        with pytest.raises(WorkflowStepError):
            await wf.run(x="test")


# ---------------------------------------------------------------------------
# WorkflowStepError
# ---------------------------------------------------------------------------


class TestDurableWorkflowSignals:
    @pytest.mark.asyncio
    async def test_step_parks_on_waiting(self):
        store = InMemoryStore()
        wf = DurableWorkflow(name="signal-test", store=store)

        @wf.step("check")
        async def check(topic: str) -> str:
            sig = wf.get_signal("approval")
            if sig is None:
                raise WorkflowWaiting("approval")
            return f"approved:{sig['verdict']}"

        with pytest.raises(WorkflowWaiting):
            await wf.run(run_id="sig-1", topic="test")

        # Verify step saved as WAITING
        run = await store.load_run("signal-test", "sig-1")
        assert run is not None
        assert run.status == StepStatus.WAITING
        assert run.steps["check"].status == StepStatus.WAITING

    @pytest.mark.asyncio
    async def test_signal_unblocks_workflow(self):
        store = InMemoryStore()
        wf = DurableWorkflow(name="signal-test", store=store)

        @wf.step("check")
        async def check(topic: str) -> str:
            sig = wf.get_signal("approval")
            if sig is None:
                raise WorkflowWaiting("approval")
            return f"approved:{sig['verdict']}"

        # First run: parks
        with pytest.raises(WorkflowWaiting):
            await wf.run(run_id="sig-2", topic="test")

        # Send signal
        await wf.signal("sig-2", "approval", {"verdict": "yes"})

        # Resume: should complete
        result = await wf.run(run_id="sig-2", topic="test")
        assert result == "approved:yes"

    @pytest.mark.asyncio
    async def test_step_saved_as_running_before_execution(self):
        """Verify step is marked RUNNING and saved before execution."""
        store = InMemoryStore()
        wf = DurableWorkflow(name="pre-save-test", store=store)
        saved_statuses = []

        original_save = store.save_run

        async def tracking_save(run):
            step = run.steps.get("step1")
            if step:
                saved_statuses.append(step.status)
            await original_save(run)

        store.save_run = tracking_save

        @wf.step("step1")
        async def step1(topic: str) -> str:
            return "done"

        await wf.run(run_id="pre-1", topic="test")
        # First save of step1 should be RUNNING, second should be COMPLETED
        assert StepStatus.RUNNING in saved_statuses
        assert StepStatus.COMPLETED in saved_statuses
        assert saved_statuses.index(StepStatus.RUNNING) < saved_statuses.index(
            StepStatus.COMPLETED
        )


class TestWaitingStatus:
    def test_waiting_status_value(self):
        assert StepStatus.WAITING == "waiting"

    def test_workflow_waiting_exception(self):
        exc = WorkflowWaiting("human_approval")
        assert exc.signal_name == "human_approval"
        assert str(exc) == "Workflow waiting for signal: human_approval"


class TestWorkflowRunFromDict:
    def test_round_trip(self):
        run = WorkflowRun(workflow_name="test", run_id="abc")
        run.steps["s1"] = StepRecord(
            step_name="s1", status=StepStatus.COMPLETED, result="done", attempts=1
        )
        run.signals = {"approval": {"ok": True}}
        d = run.to_dict()
        restored = WorkflowRun.from_dict(d)
        assert restored.workflow_name == "test"
        assert restored.run_id == "abc"
        assert restored.steps["s1"].status == StepStatus.COMPLETED
        assert restored.steps["s1"].result == "done"
        assert restored.signals == {"approval": {"ok": True}}

    def test_from_dict_empty_steps(self):
        d = {"workflow_name": "w", "run_id": "r", "status": "pending", "steps": {}, "signals": {}}
        restored = WorkflowRun.from_dict(d)
        assert restored.steps == {}
        assert restored.signals == {}

    def test_artifact_destination_round_trip(self):
        from sagewai.artifacts.models import (
            ArtifactDestination,
            ArtifactDestinationType,
        )

        dest = ArtifactDestination(
            type=ArtifactDestinationType.GITHUB,
            target="https://github.com/acme/portfolio.git",
            env_keys=["GITHUB_TOKEN"],
            options={"branch": "main"},
        )
        run = WorkflowRun(
            workflow_name="w", run_id="r", artifact_destination=dest,
        )
        d = run.to_dict()
        assert d["artifact_destination"]["type"] == "github"
        restored = WorkflowRun.from_dict(d)
        assert restored.artifact_destination == dest

    def test_artifact_destination_none_round_trip(self):
        run = WorkflowRun(workflow_name="w", run_id="r")
        d = run.to_dict()
        assert d["artifact_destination"] is None
        restored = WorkflowRun.from_dict(d)
        assert restored.artifact_destination is None


class TestRecoverStaleRuns:
    @pytest.mark.asyncio
    async def test_recover_finds_stale_running_workflows(self):
        import time

        store = InMemoryStore()
        run = WorkflowRun(workflow_name="w", run_id="stale-1", status=StepStatus.RUNNING)
        run.started_at = time.time() - 600  # 10 min ago
        await store.save_run(run)

        # Mark run as stale by backdating updated_at
        store._updated_at["w:stale-1"] = time.time() - 600

        stale = await store.recover_stale_runs(stale_timeout_seconds=300)
        assert len(stale) == 1
        assert stale[0].run_id == "stale-1"

    @pytest.mark.asyncio
    async def test_recover_skips_waiting_workflows(self):
        import time

        store = InMemoryStore()
        run = WorkflowRun(workflow_name="w", run_id="wait-1", status=StepStatus.WAITING)
        await store.save_run(run)
        store._updated_at["w:wait-1"] = time.time() - 600

        stale = await store.recover_stale_runs(stale_timeout_seconds=300)
        assert len(stale) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_prevents_stale_detection(self):
        import time

        store = InMemoryStore()
        run = WorkflowRun(workflow_name="w", run_id="fresh-1", status=StepStatus.RUNNING)
        await store.save_run(run)
        store._updated_at["w:fresh-1"] = time.time() - 600  # looks stale

        await store.heartbeat("w", "fresh-1")  # refresh

        stale = await store.recover_stale_runs(stale_timeout_seconds=300)
        assert len(stale) == 0


class TestWorkflowStepError:
    def test_attributes(self):
        err = WorkflowStepError(
            "msg",
            step_name="s1",
            workflow_name="wf1",
            run_id="r1",
        )
        assert err.step_name == "s1"
        assert err.workflow_name == "wf1"
        assert err.run_id == "r1"
        assert str(err) == "msg"


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class TestWorkflowObservability:
    @pytest.mark.asyncio
    async def test_workflow_emits_lifecycle_logs(self, caplog):
        """Workflow run emits structured log messages for state transitions."""
        import logging

        store = InMemoryStore()
        wf = DurableWorkflow(name="obs-test", store=store)

        @wf.step("s1")
        async def s1(topic: str) -> str:
            return "done"

        with caplog.at_level(logging.INFO, logger="sagewai.core.state"):
            await wf.run(run_id="obs-1", topic="test")

        messages = [r.message for r in caplog.records]
        assert any("obs-test" in m and "RUNNING" in m for m in messages)
        assert any("obs-test" in m and "COMPLETED" in m for m in messages)
