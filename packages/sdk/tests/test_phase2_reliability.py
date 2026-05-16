# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for Phase 2 reliability features: worker, monitor, DLQ, supervisor,
ConditionalAgent, YAML conditional/router, and ApprovalGate."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.dlq import DeadLetterQueue, DLQEntry
from sagewai.core.monitor import (
    ExecutionDetail,
    ExecutionSummary,
    QueueStats,
    WorkflowMonitor,
)
from sagewai.core.state import (
    ApprovalDeniedError,
    ApprovalGate,
    DurableWorkflow,
    InMemoryStore,
    StepStatus,
    WorkflowRun,
    WorkflowWaiting,
)
from sagewai.core.supervisor import WorkflowSupervisor
from sagewai.core.worker import WorkflowWorker
from sagewai.core.workflows import ConditionalAgent
from sagewai.core.yaml_workflow import (
    WorkflowParseError,
    load_workflow_string,
)
from sagewai.models.message import ChatMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAgent(BaseAgent):
    """Test agent with a predetermined response."""

    def __init__(self, name: str = "mock", response: str = "mock response", **kw):
        super().__init__(name=name, **kw)
        self._response = response
        self.calls: list[str] = []

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        return ChatMessage.assistant(self._response)

    async def chat(self, message: str) -> str:
        self.calls.append(message)
        return self._response


class EchoAgent(BaseAgent):
    """Test agent that echoes input with a prefix."""

    def __init__(self, prefix: str = "", **kwargs):
        super().__init__(**kwargs)
        self.prefix = prefix

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        return f"{self.prefix}{message}"


def _make_mock_store():
    """Create a mock PostgresStore with the methods WorkflowWorker needs."""
    store = AsyncMock()
    store.claim_pending_run = AsyncMock(return_value=None)
    store.fail_run = AsyncMock()
    store.complete_run = AsyncMock()
    store.heartbeat = AsyncMock()
    return store


# ===========================================================================
# 2a: WorkflowWorker
# ===========================================================================


class TestWorkflowWorker:
    @pytest.mark.asyncio
    async def test_worker_claims_and_executes(self):
        """Worker claims a pending run and executes it."""
        store = _make_mock_store()

        # Create a simple workflow
        wf = DurableWorkflow(name="test-wf")

        @wf.step("greet")
        async def greet(msg: str) -> str:
            return f"Hello, {msg}!"

        # Simulate a pending run that claim_pending_run returns once
        pending_run = WorkflowRun(
            workflow_name="test-wf",
            run_id="run-1",
        )
        pending_run._input = {"msg": "World"}

        # Return the run once, then None (empty queue), then set shutdown
        call_count = 0

        async def claim_side_effect(worker_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pending_run
            return None

        store.claim_pending_run = AsyncMock(side_effect=claim_side_effect)

        # We need to mock load_run so DurableWorkflow.run() works
        store.load_run = AsyncMock(return_value=None)
        store.save_run = AsyncMock()

        # Swap the workflow's store so it uses our mock
        wf._store = store

        worker = WorkflowWorker(
            store=store,
            workflow_registry={"test-wf": wf},
            max_concurrent=2,
            poll_interval=0.05,
        )

        # Run worker briefly then stop
        async def stop_soon():
            await asyncio.sleep(0.3)
            await worker.stop()

        await asyncio.gather(worker.start(), stop_soon())

        # The worker should have tried to complete the run
        assert store.complete_run.called or store.save_run.called

    @pytest.mark.asyncio
    async def test_worker_unknown_workflow(self):
        """Worker fails runs with unknown workflow names."""
        store = _make_mock_store()

        pending_run = WorkflowRun(
            workflow_name="nonexistent-wf",
            run_id="run-unk",
        )

        call_count = 0

        async def claim_once(worker_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pending_run
            return None

        store.claim_pending_run = AsyncMock(side_effect=claim_once)

        worker = WorkflowWorker(
            store=store,
            workflow_registry={},
            max_concurrent=2,
            poll_interval=0.05,
        )

        async def stop_soon():
            await asyncio.sleep(0.2)
            await worker.stop()

        await asyncio.gather(worker.start(), stop_soon())

        store.fail_run.assert_called_once_with(
            "nonexistent-wf", "run-unk", "Unknown workflow: nonexistent-wf"
        )

    @pytest.mark.asyncio
    async def test_worker_graceful_shutdown(self):
        """Worker stops after finishing in-flight work."""
        store = _make_mock_store()

        worker = WorkflowWorker(
            store=store,
            workflow_registry={},
            max_concurrent=2,
            poll_interval=0.05,
        )

        async def stop_immediately():
            await asyncio.sleep(0.05)
            await worker.stop()

        await asyncio.gather(worker.start(), stop_immediately())

        # Should not raise and should have no active tasks
        assert len(worker._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_worker_concurrency_limit(self):
        """Worker respects max_concurrent limit."""
        store = _make_mock_store()

        max_concurrent_seen = 0
        current_count = 0
        lock = asyncio.Lock()

        wf = DurableWorkflow(name="slow-wf")

        @wf.step("slow")
        async def slow_step(x: str) -> str:
            nonlocal current_count, max_concurrent_seen
            async with lock:
                current_count += 1
                max_concurrent_seen = max(
                    max_concurrent_seen, current_count
                )
            await asyncio.sleep(0.1)
            async with lock:
                current_count -= 1
            return "done"

        wf._store = store
        store.load_run = AsyncMock(return_value=None)
        store.save_run = AsyncMock()

        runs_yielded = 0

        async def claim_many(worker_id, **kwargs):
            nonlocal runs_yielded
            runs_yielded += 1
            if runs_yielded <= 6:
                r = WorkflowRun(
                    workflow_name="slow-wf",
                    run_id=f"run-{runs_yielded}",
                )
                r._input = {"x": "test"}
                return r
            return None

        store.claim_pending_run = AsyncMock(side_effect=claim_many)

        worker = WorkflowWorker(
            store=store,
            workflow_registry={"slow-wf": wf},
            max_concurrent=2,
            poll_interval=0.02,
        )

        async def stop_after():
            await asyncio.sleep(0.8)
            await worker.stop()

        await asyncio.gather(worker.start(), stop_after())

        # The semaphore should have limited concurrency to 2
        assert max_concurrent_seen <= 2


# ===========================================================================
# 2b: WorkflowMonitor
# ===========================================================================


class TestWorkflowMonitor:
    @pytest.mark.asyncio
    async def test_monitor_list_executions(self):
        """Monitor lists executions from store."""
        store = AsyncMock()
        store.list_all_runs = AsyncMock(
            return_value=[
                {
                    "run_id": "r1",
                    "workflow_name": "wf1",
                    "status": "completed",
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:01:00",
                },
                {
                    "run_id": "r2",
                    "workflow_name": "wf1",
                    "status": "failed",
                    "created_at": "2026-01-01T00:02:00",
                    "updated_at": "2026-01-01T00:03:00",
                    "error": "boom",
                },
            ]
        )

        monitor = WorkflowMonitor(store=store)
        results = await monitor.list_executions(workflow_name="wf1")

        assert len(results) == 2
        assert results[0].run_id == "r1"
        assert results[0].status == "completed"
        assert results[1].error == "boom"

    @pytest.mark.asyncio
    async def test_monitor_list_executions_no_support(self):
        """Monitor returns empty list when store lacks list_all_runs."""
        store = MagicMock(spec=[])  # no attributes
        monitor = WorkflowMonitor(store=store)
        results = await monitor.list_executions()
        assert results == []

    @pytest.mark.asyncio
    async def test_monitor_get_execution(self):
        """Monitor retrieves execution detail."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(
            return_value={
                "run_id": "r1",
                "workflow_name": "wf1",
                "status": "completed",
                "input": {"topic": "test"},
                "output": {"result": "done"},
                "data": {
                    "steps": {
                        "step1": {
                            "status": "completed",
                            "result": "ok",
                            "attempts": 1,
                            "started_at": 1000.0,
                            "completed_at": 1001.5,
                        }
                    }
                },
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
            }
        )

        monitor = WorkflowMonitor(store=store)
        detail = await monitor.get_execution("r1")

        assert detail is not None
        assert detail.run_id == "r1"
        assert len(detail.steps) == 1
        assert detail.steps[0].step_name == "step1"
        assert detail.steps[0].duration_seconds == 1.5

    @pytest.mark.asyncio
    async def test_monitor_get_execution_not_found(self):
        """Monitor returns None when run not found."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(return_value=None)
        monitor = WorkflowMonitor(store=store)
        assert await monitor.get_execution("missing") is None

    @pytest.mark.asyncio
    async def test_monitor_queue_stats(self):
        """Monitor returns queue statistics."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"status": "pending", "cnt": 5},
                {"status": "running", "cnt": 2},
                {"status": "completed", "cnt": 10},
                {"status": "failed", "cnt": 1},
            ]
        )
        store = MagicMock()
        store._pool = mock_pool

        monitor = WorkflowMonitor(store=store)
        stats = await monitor.get_queue_stats()

        assert stats.pending == 5
        assert stats.running == 2
        assert stats.completed == 10
        assert stats.failed == 1
        assert stats.total == 18

    @pytest.mark.asyncio
    async def test_monitor_queue_stats_no_pool(self):
        """Monitor returns empty stats when store has no pool."""
        store = MagicMock(spec=[])
        monitor = WorkflowMonitor(store=store)
        stats = await monitor.get_queue_stats()
        assert stats.total == 0

    @pytest.mark.asyncio
    async def test_monitor_retry_execution(self):
        """Monitor can retry a failed execution."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(
            return_value={
                "run_id": "r-fail",
                "workflow_name": "wf1",
                "status": "failed",
                "input": {"topic": "retry-me"},
                "steps_total": 3,
                "created_at": "2026-01-01",
                "updated_at": "2026-01-01",
            }
        )
        store.enqueue_run = AsyncMock()

        monitor = WorkflowMonitor(store=store)
        new_id = await monitor.retry_execution("r-fail")

        assert new_id == "r-fail-retry-1"
        store.enqueue_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_monitor_retry_non_failed_raises(self):
        """Monitor rejects retrying a non-failed execution."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(
            return_value={
                "run_id": "r-ok",
                "workflow_name": "wf1",
                "status": "completed",
            }
        )
        monitor = WorkflowMonitor(store=store)

        with pytest.raises(ValueError, match="Can only retry failed"):
            await monitor.retry_execution("r-ok")

    @pytest.mark.asyncio
    async def test_monitor_terminate_execution(self):
        """Monitor can cancel a running execution."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(
            return_value={
                "run_id": "r-stuck",
                "workflow_name": "wf1",
                "status": "running",
            }
        )
        store.cancel_run = AsyncMock(return_value=True)

        monitor = WorkflowMonitor(store=store)
        result = await monitor.terminate_execution("r-stuck")
        assert result is True

    @pytest.mark.asyncio
    async def test_monitor_terminate_not_found(self):
        """Monitor returns False when terminating a non-existent run."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(return_value=None)
        store.cancel_run = AsyncMock()

        monitor = WorkflowMonitor(store=store)
        result = await monitor.terminate_execution("missing")
        assert result is False


# ===========================================================================
# 2d: DLQ
# ===========================================================================


class TestDeadLetterQueue:
    @pytest.mark.asyncio
    async def test_dlq_move_and_list(self):
        """DLQ can store and list entries."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=42)
        mock_pool.fetch = AsyncMock(
            return_value=[
                {
                    "id": 42,
                    "run_id": "r-fail",
                    "workflow_name": "wf1",
                    "input_data": json.dumps({"topic": "test"}),
                    "error": "Step failed",
                    "original_data": json.dumps({}),
                    "retry_count": 0,
                    "created_at": "2026-01-01T00:00:00",
                },
            ]
        )

        store = AsyncMock()
        store._pool = mock_pool
        store.get_run_by_run_id = AsyncMock(
            return_value={
                "run_id": "r-fail",
                "input": {"topic": "test"},
                "data": {},
            }
        )

        dlq = DeadLetterQueue(store=store)

        # Move to DLQ
        entry_id = await dlq.move_to_dlq("wf1", "r-fail", "Step failed")
        assert entry_id == 42
        mock_pool.fetchval.assert_called_once()

        # List entries
        entries = await dlq.list_entries()
        assert len(entries) == 1
        assert entries[0].run_id == "r-fail"
        assert entries[0].error == "Step failed"
        assert entries[0].input_data == {"topic": "test"}

    @pytest.mark.asyncio
    async def test_dlq_move_not_found(self):
        """DLQ raises ValueError when run not found."""
        store = AsyncMock()
        store.get_run_by_run_id = AsyncMock(return_value=None)

        dlq = DeadLetterQueue(store=store)
        with pytest.raises(ValueError, match="Run not found"):
            await dlq.move_to_dlq("wf1", "missing", "error")

    @pytest.mark.asyncio
    async def test_dlq_retry(self):
        """DLQ retry re-enqueues with incremented count."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": 1,
                "run_id": "r-fail",
                "workflow_name": "wf1",
                "input_data": {"topic": "retry-me"},
                "error": "boom",
                "original_data": {},
                "retry_count": 0,
                "created_at": "2026-01-01",
            }
        )
        mock_pool.execute = AsyncMock()

        store = AsyncMock()
        store._pool = mock_pool
        store.enqueue_run = AsyncMock()

        dlq = DeadLetterQueue(store=store)
        new_id = await dlq.retry("r-fail")

        assert new_id == "r-fail-retry-1"
        store.enqueue_run.assert_called_once()
        # Verify retry_count was incremented
        mock_pool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_retry_not_found(self):
        """DLQ retry raises ValueError when entry not found."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        store = AsyncMock()
        store._pool = mock_pool

        dlq = DeadLetterQueue(store=store)
        with pytest.raises(ValueError, match="DLQ entry not found"):
            await dlq.retry("missing")

    @pytest.mark.asyncio
    async def test_dlq_discard(self):
        """DLQ discard removes an entry."""
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")

        store = AsyncMock()
        store._pool = mock_pool

        dlq = DeadLetterQueue(store=store)
        result = await dlq.discard("r-fail")
        assert result is True

    @pytest.mark.asyncio
    async def test_dlq_count(self):
        """DLQ count returns total entries."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=7)

        store = AsyncMock()
        store._pool = mock_pool

        dlq = DeadLetterQueue(store=store)
        assert await dlq.count() == 7

    @pytest.mark.asyncio
    async def test_dlq_list_filtered_by_workflow(self):
        """DLQ list_entries filters by workflow_name."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        store = AsyncMock()
        store._pool = mock_pool

        dlq = DeadLetterQueue(store=store)
        entries = await dlq.list_entries(workflow_name="wf1")
        assert entries == []

        # Should have passed workflow_name as $3
        call_args = mock_pool.fetch.call_args
        assert "wf1" in call_args[0]


# ===========================================================================
# 2e: ConditionalAgent
# ===========================================================================


class TestConditionalAgent:
    @pytest.mark.asyncio
    async def test_conditional_agent_routes_correctly(self):
        """ConditionalAgent routes to correct branch."""
        agent_a = MockAgent(name="a", response="branch A")
        agent_b = MockAgent(name="b", response="branch B")

        router = ConditionalAgent(
            name="router",
            condition=lambda text: "a" if "hello" in text.lower() else "b",
            branches={"a": agent_a, "b": agent_b},
        )

        result = await router.chat("Hello world")
        assert result == "branch A"

        result = await router.chat("Goodbye")
        assert result == "branch B"

    @pytest.mark.asyncio
    async def test_conditional_agent_default_branch(self):
        """ConditionalAgent uses default when no branch matches."""
        agent_a = MockAgent(name="a", response="branch A")
        fallback = MockAgent(name="fallback", response="default response")

        router = ConditionalAgent(
            name="router",
            condition=lambda text: "nonexistent",
            branches={"a": agent_a},
            default_branch=fallback,
        )

        result = await router.chat("anything")
        assert result == "default response"

    @pytest.mark.asyncio
    async def test_conditional_agent_no_match_no_default(self):
        """ConditionalAgent returns message when no branch matches and no default."""
        agent_a = MockAgent(name="a", response="branch A")

        router = ConditionalAgent(
            name="router",
            condition=lambda text: "nonexistent",
            branches={"a": agent_a},
        )

        result = await router.chat("anything")
        assert "No branch matched" in result

    @pytest.mark.asyncio
    async def test_conditional_agent_async_condition(self):
        """ConditionalAgent handles async condition functions."""

        async def async_condition(text: str) -> str:
            return "positive" if "good" in text.lower() else "negative"

        pos_agent = MockAgent(name="pos", response="positive!")
        neg_agent = MockAgent(name="neg", response="negative!")

        router = ConditionalAgent(
            name="router",
            condition=async_condition,
            branches={"positive": pos_agent, "negative": neg_agent},
        )

        result = await router.chat("This is good")
        assert result == "positive!"

        result = await router.chat("This is bad")
        assert result == "negative!"

    @pytest.mark.asyncio
    async def test_conditional_agent_streaming(self):
        """ConditionalAgent.chat_stream works."""
        agent_a = MockAgent(name="a", response="streamed-result")

        router = ConditionalAgent(
            name="router",
            condition=lambda text: "a",
            branches={"a": agent_a},
        )

        chunks = []
        async for chunk in router.chat_stream("hello"):
            chunks.append(chunk)

        full = "".join(chunks)
        assert "streamed-result" in full


# ===========================================================================
# 2e: YAML conditional/router
# ===========================================================================


class TestYamlConditionalNode:
    def test_yaml_conditional_contains(self):
        """YAML DSL parses conditional nodes with contains condition."""
        yaml_str = """
name: conditional-test
description: Test conditional routing

agents:
  handler_a:
    model: gpt-4o
    system_prompt: "Handle errors"
  handler_b:
    model: gpt-4o
    system_prompt: "Handle normal"

workflow:
  type: conditional
  condition:
    contains: "error"
  then:
    agent: handler_a
  else:
    agent: handler_b
"""
        spec = load_workflow_string(yaml_str)
        assert spec.name == "conditional-test"
        wf = spec.workflow
        assert isinstance(wf, ConditionalAgent)
        assert "then" in wf._branches
        assert "else" in wf._branches

    def test_yaml_conditional_regex(self):
        """YAML DSL parses conditional with regex condition."""
        yaml_str = """
name: regex-test
description: Regex conditional

agents:
  match_agent:
    model: gpt-4o
    system_prompt: "Match handler"
  no_match:
    model: gpt-4o
    system_prompt: "No match handler"

workflow:
  type: conditional
  condition:
    regex: "\\\\d{3}-\\\\d{4}"
  then:
    agent: match_agent
  else:
    agent: no_match
"""
        spec = load_workflow_string(yaml_str)
        assert isinstance(spec.workflow, ConditionalAgent)

    def test_yaml_conditional_missing_then(self):
        """YAML conditional without 'then' raises error."""
        yaml_str = """
name: bad-conditional
description: Missing then

agents:
  handler:
    model: gpt-4o

workflow:
  type: conditional
  condition:
    contains: "test"
"""
        with pytest.raises(WorkflowParseError, match="'then' branch"):
            load_workflow_string(yaml_str)

    def test_yaml_conditional_bad_condition(self):
        """YAML conditional without valid condition type raises error."""
        yaml_str = """
name: bad-condition
description: Bad condition

agents:
  handler:
    model: gpt-4o

workflow:
  type: conditional
  condition:
    invalid_key: "test"
  then:
    agent: handler
"""
        with pytest.raises(
            WorkflowParseError, match="contains, regex, equals"
        ):
            load_workflow_string(yaml_str)

    def test_yaml_router_node(self):
        """YAML DSL parses router nodes."""
        yaml_str = """
name: router-test
description: LLM router

agents:
  tech_agent:
    model: gpt-4o
    system_prompt: "Technical support"
  billing_agent:
    model: gpt-4o
    system_prompt: "Billing support"

workflow:
  type: router
  model: gpt-4o-mini
  prompt: "Classify as: technical, billing"
  routes:
    technical:
      agent: tech_agent
    billing:
      agent: billing_agent
"""
        spec = load_workflow_string(yaml_str)
        assert spec.name == "router-test"
        wf = spec.workflow
        assert isinstance(wf, ConditionalAgent)
        assert "technical" in wf._branches
        assert "billing" in wf._branches

    def test_yaml_router_missing_prompt(self):
        """YAML router without prompt raises error."""
        yaml_str = """
name: bad-router
description: No prompt

agents:
  agent1:
    model: gpt-4o

workflow:
  type: router
  routes:
    default:
      agent: agent1
"""
        with pytest.raises(WorkflowParseError, match="'prompt'"):
            load_workflow_string(yaml_str)

    def test_yaml_router_missing_routes(self):
        """YAML router without routes raises error."""
        yaml_str = """
name: bad-router
description: No routes

agents:
  agent1:
    model: gpt-4o

workflow:
  type: router
  prompt: "Classify input"
"""
        with pytest.raises(WorkflowParseError, match="'routes'"):
            load_workflow_string(yaml_str)


# ===========================================================================
# 2f: ApprovalGate
# ===========================================================================


class TestApprovalGate:
    @pytest.mark.asyncio
    async def test_approval_gate_blocks_workflow(self):
        """ApprovalGate raises WorkflowWaiting."""
        store = InMemoryStore()
        wf = DurableWorkflow(name="approval-test", store=store)
        gate = ApprovalGate(workflow=wf)

        @wf.step("needs_approval")
        async def needs_approval(content: str) -> str:
            await gate.request_approval(
                prompt=f"Approve: {content}",
            )
            return f"approved: {content}"

        with pytest.raises(WorkflowWaiting):
            await wf.run(run_id="gate-1", content="draft article")

        # Verify run is in WAITING state
        run = await store.load_run("approval-test", "gate-1")
        assert run is not None
        assert run.status == StepStatus.WAITING

    @pytest.mark.asyncio
    async def test_approval_gate_approve_resumes(self):
        """Approved workflow continues execution."""
        store = InMemoryStore()
        wf = DurableWorkflow(name="approval-test", store=store)
        gate = ApprovalGate(workflow=wf)

        @wf.step("needs_approval")
        async def needs_approval(content: str) -> str:
            data = await gate.request_approval(prompt="Approve?")
            return f"approved by {data.get('reviewer', 'unknown')}"

        # First run: blocks
        with pytest.raises(WorkflowWaiting):
            await wf.run(run_id="gate-2", content="article")

        # Approve
        await gate.approve("gate-2", reviewer="admin")

        # Resume: should complete
        result = await wf.run(run_id="gate-2", content="article")
        assert result == "approved by admin"

    @pytest.mark.asyncio
    async def test_approval_gate_reject_raises(self):
        """Rejected workflow raises WorkflowStepError wrapping denial."""
        from sagewai.core.state import WorkflowStepError

        store = InMemoryStore()
        wf = DurableWorkflow(name="approval-test", store=store)
        gate = ApprovalGate(workflow=wf)

        @wf.step("needs_approval")
        async def needs_approval(content: str) -> str:
            await gate.request_approval(prompt="Approve?")
            return "should not reach here"

        # First run: blocks
        with pytest.raises(WorkflowWaiting):
            await wf.run(run_id="gate-3", content="bad draft")

        # Reject
        await gate.reject(
            "gate-3", reason="Quality too low", reviewer="editor"
        )

        # Resume: ApprovalDeniedError is caught by _execute_step's
        # generic except handler, so it surfaces as WorkflowStepError
        with pytest.raises(WorkflowStepError) as exc_info:
            await wf.run(run_id="gate-3", content="bad draft")

        assert "Approval denied" in str(exc_info.value)
        assert "Quality too low" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_approval_denied_error_attributes(self):
        """ApprovalDeniedError has correct attributes."""
        err = ApprovalDeniedError("reason text", reviewer="admin")
        assert err.reason == "reason text"
        assert err.reviewer == "admin"
        assert "Approval denied" in str(err)


# ===========================================================================
# 2g: WorkflowSupervisor
# ===========================================================================


class TestWorkflowSupervisor:
    @pytest.mark.asyncio
    async def test_supervisor_runs_and_stops(self):
        """Supervisor starts and stops cleanly."""
        store = AsyncMock()
        store.reset_stale_to_pending = AsyncMock(return_value=0)
        store.count_by_status = AsyncMock(
            return_value={"pending": 0, "running": 0, "failed": 0}
        )

        supervisor = WorkflowSupervisor(
            store=store,
            check_interval=0.05,
            stale_timeout=300,
        )

        async def stop_soon():
            await asyncio.sleep(0.15)
            await supervisor.stop()

        await asyncio.gather(supervisor.start(), stop_soon())

        assert not supervisor.is_running
        # Should have called reset_stale_to_pending at least once
        assert store.reset_stale_to_pending.call_count >= 1

    @pytest.mark.asyncio
    async def test_supervisor_run_once(self):
        """Supervisor single check cycle works."""
        store = AsyncMock()
        store.reset_stale_to_pending = AsyncMock(return_value=3)
        store.count_by_status = AsyncMock(
            return_value={"pending": 3, "running": 0, "failed": 0}
        )

        stale_counts: list[int] = []

        async def on_stale(count: int):
            stale_counts.append(count)

        supervisor = WorkflowSupervisor(
            store=store,
            on_stale_detected=on_stale,
        )

        await supervisor.run_once()

        store.reset_stale_to_pending.assert_called_once_with(300)
        assert stale_counts == [3]

    @pytest.mark.asyncio
    async def test_supervisor_no_stale_support(self):
        """Supervisor handles stores without reset_stale_to_pending."""
        store = MagicMock(spec=[])  # no methods

        supervisor = WorkflowSupervisor(store=store)
        # Should not raise
        await supervisor.run_once()

    @pytest.mark.asyncio
    async def test_supervisor_callback_error_handled(self):
        """Supervisor handles callback errors gracefully."""
        store = AsyncMock()
        store.reset_stale_to_pending = AsyncMock(return_value=1)

        async def bad_callback(count: int):
            raise RuntimeError("callback failed")

        supervisor = WorkflowSupervisor(
            store=store,
            on_stale_detected=bad_callback,
        )

        # Should not raise despite callback failure
        await supervisor.run_once()

    @pytest.mark.asyncio
    async def test_supervisor_is_running_property(self):
        """Supervisor.is_running reflects actual state."""
        store = AsyncMock()
        store.reset_stale_to_pending = AsyncMock(return_value=0)

        supervisor = WorkflowSupervisor(
            store=store, check_interval=0.05
        )

        assert not supervisor.is_running

        started = asyncio.Event()

        original_check = supervisor._check_health

        async def mark_started():
            started.set()
            await original_check()

        supervisor._check_health = mark_started

        async def verify_and_stop():
            await started.wait()
            assert supervisor.is_running
            await supervisor.stop()

        await asyncio.gather(supervisor.start(), verify_and_stop())
        assert not supervisor.is_running
