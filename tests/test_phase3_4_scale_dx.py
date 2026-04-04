"""Tests for Phase 3-4 scale & DX changes across the sagewai SDK.

Covers:
  3a  Rate limiting (TokenBucketLimiter, SlidingWindowLimiter, RateLimiter)
  3b  Multi-tenancy (WorkflowRun.project_id, AgentConfig.project_id)
  3c  Audit logging (AuditEvent, AuditLogger, InMemoryAuditBackend)
  3d  Backpressure (QueueFullError)
  3e  Archiver (LocalArchiveBackend, ArchiveConfig, helpers)
  4a  Visualization (workflow_to_mermaid, execution_to_mermaid)
  4b  Progress reporting (DurableRunner.on_progress)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import pytest

from sagewai.core.base import BaseAgent
from sagewai.core.durability import DurableRunner
from sagewai.core.events import AgentEvent
from sagewai.core.rate_limiter import (
    RateLimiter,
    SlidingWindowLimiter,
    TokenBucketLimiter,
)
from sagewai.core.state import InMemoryStore, QueueFullError, WorkflowRun
from sagewai.core.visualize import (
    execution_to_mermaid,
    workflow_to_mermaid,
    workflow_to_mermaid_from_yaml,
)
from sagewai.models.agent import AgentConfig
from sagewai.observability.archiver import (
    ArchiveConfig,
    LocalArchiveBackend,
    _to_jsonl,
    _to_parquet,
)
from sagewai.observability.audit import (
    AuditEvent,
    AuditLogger,
    InMemoryAuditBackend,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockAgent(BaseAgent):
    """Agent with predetermined response for testing."""

    def __init__(
        self,
        name: str = "mock",
        response: str = "mock response",
        delay: float = 0.0,
        **kwargs: Any,
    ):
        super().__init__(name=name, **kwargs)
        self._response = response
        self._delay = delay
        self.call_count = 0

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        raise NotImplementedError

    async def chat(self, message: str) -> str:
        self.call_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._response

    async def chat_stream(self, message: str) -> AsyncGenerator[str, None]:
        self.call_count += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        words = self._response.split()
        for word in words:
            yield word + " "


# ===================================================================
# 3a: Rate Limiter — TokenBucketLimiter
# ===================================================================


class TestTokenBucketLimiter:
    def test_acquire_within_rate(self):
        """Tokens available within rate limit."""
        limiter = TokenBucketLimiter(rate=10, period=60.0)
        # Should be able to acquire up to 10 tokens (burst = rate)
        for _ in range(10):
            assert limiter.try_acquire() is True

    def test_try_acquire_exhausts_tokens(self):
        """try_acquire returns False when tokens exhausted."""
        limiter = TokenBucketLimiter(rate=3, period=60.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_tokens_refill_over_time(self):
        """Tokens refill based on elapsed time."""
        limiter = TokenBucketLimiter(rate=10, period=1.0)
        # Exhaust all tokens
        for _ in range(10):
            limiter.try_acquire()
        assert limiter.try_acquire() is False

        # Simulate time passing by adjusting _last_refill
        limiter._last_refill = time.monotonic() - 0.5
        # After 0.5s with rate=10/1s, ~5 tokens should be available
        assert limiter.available_tokens >= 4.0

    @pytest.mark.asyncio
    async def test_async_acquire_blocks(self):
        """acquire() blocks until tokens available."""
        limiter = TokenBucketLimiter(rate=10, period=1.0)
        # Exhaust tokens
        for _ in range(10):
            limiter.try_acquire()

        # acquire should eventually return (after refill)
        # Move time back so refill happens quickly
        limiter._last_refill = time.monotonic() - 1.0
        await asyncio.wait_for(limiter.acquire(), timeout=2.0)
        # If we get here without timeout, the test passes

    def test_time_until_available(self):
        """Correctly estimates wait time."""
        limiter = TokenBucketLimiter(rate=10, period=10.0)
        # With full tokens, time should be 0
        assert limiter.time_until_available() == 0.0

        # Exhaust all tokens
        for _ in range(10):
            limiter.try_acquire()

        # Now should estimate non-zero wait time
        wait = limiter.time_until_available()
        assert wait > 0.0

    def test_burst_size(self):
        """Burst parameter allows extra tokens beyond rate."""
        limiter = TokenBucketLimiter(rate=5, period=60.0, burst=10)
        # Should be able to acquire up to burst size
        for _ in range(10):
            assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_custom_name(self):
        """Limiter name is set correctly."""
        limiter = TokenBucketLimiter(rate=5, period=60.0, name="my_limiter")
        assert limiter.name == "my_limiter"


# ===================================================================
# 3a: Rate Limiter — SlidingWindowLimiter
# ===================================================================


class TestSlidingWindowLimiter:
    def test_within_window(self):
        """Allows calls within window."""
        limiter = SlidingWindowLimiter(max_calls=5, window_seconds=60.0)
        for _ in range(5):
            assert limiter.try_acquire() is True

    def test_exceeds_window(self):
        """Rejects calls exceeding window."""
        limiter = SlidingWindowLimiter(max_calls=3, window_seconds=60.0)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_remaining_count(self):
        """Correctly reports remaining calls."""
        limiter = SlidingWindowLimiter(max_calls=5, window_seconds=60.0)
        assert limiter.remaining == 5
        limiter.try_acquire()
        assert limiter.remaining == 4
        limiter.try_acquire()
        assert limiter.remaining == 3

    def test_current_count(self):
        """Correctly reports current call count."""
        limiter = SlidingWindowLimiter(max_calls=10, window_seconds=60.0)
        assert limiter.current_count == 0
        limiter.try_acquire()
        limiter.try_acquire()
        assert limiter.current_count == 2

    def test_window_expiry(self):
        """Old timestamps expire outside the window."""
        limiter = SlidingWindowLimiter(max_calls=2, window_seconds=1.0)
        limiter.try_acquire()
        limiter.try_acquire()
        assert limiter.try_acquire() is False

        # Simulate timestamps aging out by pushing them back
        old_time = time.monotonic() - 2.0
        limiter._timestamps.clear()
        limiter._timestamps.append(old_time)
        limiter._timestamps.append(old_time)
        # After cleanup, should allow new calls
        assert limiter.try_acquire() is True


# ===================================================================
# 3a: Rate Limiter — RateLimiter (composite)
# ===================================================================


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_composite_llm_check(self):
        """Composite limiter checks LLM rate."""
        llm_limiter = TokenBucketLimiter(rate=5, period=60.0)
        limiter = RateLimiter(llm_limiter=llm_limiter)

        # Should be able to make LLM calls within limit
        for _ in range(5):
            await limiter.check_llm()

        # Non-blocking check should now fail
        assert limiter.try_llm() is False

    @pytest.mark.asyncio
    async def test_composite_tool_check(self):
        """Composite limiter checks tool rate."""
        tool_limiter = SlidingWindowLimiter(
            max_calls=3, window_seconds=60.0
        )
        limiter = RateLimiter(tool_limiter=tool_limiter)

        for _ in range(3):
            await limiter.check_tool()

        assert limiter.try_tool() is False

    def test_no_limiter_passes(self):
        """No limiter configured always passes."""
        limiter = RateLimiter()
        assert limiter.try_llm() is True
        assert limiter.try_tool() is True

    @pytest.mark.asyncio
    async def test_no_limiter_check_passes(self):
        """Async check with no limiter completes immediately."""
        limiter = RateLimiter()
        await limiter.check_llm()
        await limiter.check_tool()

    def test_composite_name(self):
        """Composite limiter stores name."""
        limiter = RateLimiter(name="project-42")
        assert limiter.name == "project-42"


# ===================================================================
# 3b: Multi-tenancy (project scoping)
# ===================================================================


class TestMultiTenancyProjectScoping:
    def test_workflow_run_project_id(self):
        """WorkflowRun has project_id field."""
        run = WorkflowRun(
            workflow_name="test-wf",
            run_id="run-1",
            project_id="acme-corp",
        )
        assert run.project_id == "acme-corp"

    def test_workflow_run_project_id_default(self):
        """WorkflowRun project_id defaults to None."""
        run = WorkflowRun(workflow_name="wf", run_id="r1")
        assert run.project_id is None

    def test_workflow_run_serialization_with_project(self):
        """project_id survives to_dict/from_dict round-trip."""
        run = WorkflowRun(
            workflow_name="test-wf",
            run_id="run-1",
            project_id="acme-corp",
        )
        data = run.to_dict()
        assert data["project_id"] == "acme-corp"

        restored = WorkflowRun.from_dict(data)
        assert restored.project_id == "acme-corp"
        assert restored.workflow_name == "test-wf"
        assert restored.run_id == "run-1"

    def test_workflow_run_serialization_without_project(self):
        """Round-trip works when project_id is None."""
        run = WorkflowRun(workflow_name="wf", run_id="r2")
        data = run.to_dict()
        restored = WorkflowRun.from_dict(data)
        assert restored.project_id is None

    def test_agent_config_project_id(self):
        """AgentConfig has project_id field."""
        config = AgentConfig(name="test-agent", project_id="project-123")
        assert config.project_id == "project-123"

    def test_agent_config_project_id_default(self):
        """AgentConfig project_id defaults to None."""
        config = AgentConfig(name="test-agent")
        assert config.project_id is None


# ===================================================================
# 3c: Audit Logging — AuditEvent
# ===================================================================


class TestAuditEvent:
    def test_event_defaults(self):
        """AuditEvent has sensible defaults."""
        event = AuditEvent(action="llm_call")
        assert event.action == "llm_call"
        assert event.agent_name == ""
        assert event.model == ""
        assert event.status == "success"
        assert event.tokens_used == 0
        assert event.cost_usd == 0.0
        assert event.timestamp > 0
        assert event.metadata == {}

    def test_event_serialization(self):
        """AuditEvent serializes to JSON."""
        event = AuditEvent(
            action="tool_call",
            agent_name="researcher",
            tool_name="web_search",
            project_id="acme",
            tokens_used=150,
        )
        json_str = event.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["action"] == "tool_call"
        assert parsed["agent_name"] == "researcher"
        assert parsed["tool_name"] == "web_search"
        assert parsed["project_id"] == "acme"
        assert parsed["tokens_used"] == 150

    def test_event_with_error(self):
        """AuditEvent captures error state."""
        event = AuditEvent(
            action="llm_call",
            status="error",
            error="Rate limit exceeded",
        )
        assert event.status == "error"
        assert event.error == "Rate limit exceeded"


# ===================================================================
# 3c: Audit Logging — AuditLogger
# ===================================================================


class TestAuditLogger:
    def test_log_and_retrieve(self):
        """Logger buffers events."""
        logger = AuditLogger()
        event1 = AuditEvent(action="llm_call", agent_name="agent1")
        event2 = AuditEvent(action="tool_call", agent_name="agent2")
        logger.log(event1)
        logger.log(event2)
        assert len(logger.events) == 2
        assert logger.events[0].agent_name == "agent1"
        assert logger.events[1].agent_name == "agent2"

    def test_export_jsonl(self, tmp_path):
        """JSONL export writes correct format."""
        audit = AuditLogger()
        audit.log(AuditEvent(action="llm_call", agent_name="a1"))
        audit.log(AuditEvent(action="tool_call", agent_name="a2"))

        output_file = str(tmp_path / "audit.jsonl")
        count = audit.export_jsonl(output_file)
        assert count == 2

        with open(output_file) as f:
            lines = f.readlines()
        assert len(lines) == 2
        parsed_1 = json.loads(lines[0])
        assert parsed_1["action"] == "llm_call"
        parsed_2 = json.loads(lines[1])
        assert parsed_2["action"] == "tool_call"

    @pytest.mark.asyncio
    async def test_flush_to_backend(self):
        """Flush sends events to backend."""
        backend = InMemoryAuditBackend()
        audit = AuditLogger(backends=[backend])
        audit.log(AuditEvent(action="llm_call"))
        audit.log(AuditEvent(action="tool_call"))

        await audit.flush()
        assert len(backend.events) == 2
        # Buffer should be cleared after flush
        assert len(audit.events) == 0

    def test_create_event_hook(self):
        """Event hook maps AgentEvent to AuditEvent."""
        audit = AuditLogger()
        hook = audit.create_event_hook(
            project_id="acme", user_id="user-1"
        )
        assert callable(hook)

    @pytest.mark.asyncio
    async def test_event_hook_llm_call(self):
        """Event hook correctly maps LLM_CALL_FINISHED."""
        audit = AuditLogger()
        hook = audit.create_event_hook(
            project_id="acme", user_id="user-1"
        )

        await hook(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "agent": "researcher",
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.005,
                "duration_ms": 1200.0,
            },
        )

        assert len(audit.events) == 1
        event = audit.events[0]
        assert event.action == "llm_call"
        assert event.project_id == "acme"
        assert event.user_id == "user-1"
        assert event.model == "gpt-4o"
        assert event.tokens_used == 150
        assert event.cost_usd == 0.005

    @pytest.mark.asyncio
    async def test_event_hook_tool_call(self):
        """Event hook correctly maps TOOL_CALL_RESULT."""
        audit = AuditLogger()
        hook = audit.create_event_hook(project_id="corp")

        await hook(
            AgentEvent.TOOL_CALL_RESULT,
            {
                "agent": "assistant",
                "tool_name": "web_search",
                "content": "Found 5 results",
            },
        )

        assert len(audit.events) == 1
        event = audit.events[0]
        assert event.action == "tool_call"
        assert event.tool_name == "web_search"
        assert event.status == "success"

    @pytest.mark.asyncio
    async def test_event_hook_tool_error(self):
        """Event hook captures tool errors."""
        audit = AuditLogger()
        hook = audit.create_event_hook()

        await hook(
            AgentEvent.TOOL_CALL_RESULT,
            {
                "agent": "bot",
                "tool_name": "api_call",
                "error": "Connection refused",
            },
        )

        event = audit.events[0]
        assert event.status == "error"
        assert event.error == "Connection refused"

    @pytest.mark.asyncio
    async def test_event_hook_run_error(self):
        """Event hook captures RUN_ERROR events."""
        audit = AuditLogger()
        hook = audit.create_event_hook()

        await hook(
            AgentEvent.RUN_ERROR,
            {"agent": "bot", "error": "Out of memory"},
        )

        event = audit.events[0]
        assert event.action == "agent_run_error"
        assert event.status == "error"
        assert event.error == "Out of memory"

    @pytest.mark.asyncio
    async def test_event_hook_unmapped_event(self):
        """Event hook ignores unmapped AgentEvent types."""
        audit = AuditLogger()
        hook = audit.create_event_hook()

        await hook(AgentEvent.STEP_STARTED, {"agent": "bot"})
        assert len(audit.events) == 0

    @pytest.mark.asyncio
    async def test_in_memory_backend(self):
        """InMemoryAuditBackend stores events."""
        backend = InMemoryAuditBackend()
        events = [
            AuditEvent(action="llm_call"),
            AuditEvent(action="tool_call"),
        ]
        await backend.write(events)
        assert len(backend.events) == 2

        # Write more
        await backend.write([AuditEvent(action="workflow_started")])
        assert len(backend.events) == 3


# ===================================================================
# 3d: Backpressure — QueueFullError
# ===================================================================


class TestBackpressure:
    def test_queue_full_error(self):
        """QueueFullError has correct attributes."""
        err = QueueFullError(current_depth=50, max_depth=50)
        assert err.current_depth == 50
        assert err.max_depth == 50

    def test_queue_full_error_message(self):
        """Error message includes depth info."""
        err = QueueFullError(current_depth=100, max_depth=100)
        msg = str(err)
        assert "100" in msg
        assert "Queue full" in msg

    def test_queue_full_error_is_exception(self):
        """QueueFullError can be raised and caught."""
        with pytest.raises(QueueFullError) as exc_info:
            raise QueueFullError(current_depth=10, max_depth=10)
        assert exc_info.value.current_depth == 10

    def test_queue_full_different_values(self):
        """QueueFullError with different current vs max."""
        err = QueueFullError(current_depth=25, max_depth=20)
        assert err.current_depth == 25
        assert err.max_depth == 20
        assert "25" in str(err)
        assert "20" in str(err)


# ===================================================================
# 3e: Archiver — LocalArchiveBackend
# ===================================================================


class TestLocalArchiveBackend:
    @pytest.mark.asyncio
    async def test_write_and_read(self, tmp_path):
        """Write data and read it back."""
        backend = LocalArchiveBackend(str(tmp_path))
        data = b'{"key": "value"}\n'
        written = await backend.write("test/data.jsonl", data)
        assert written == len(data)

        result = await backend.read("test/data.jsonl")
        assert result == data

    @pytest.mark.asyncio
    async def test_list_files(self, tmp_path):
        """List files under prefix."""
        backend = LocalArchiveBackend(str(tmp_path))
        await backend.write("workflows/2026-01-01/runs.jsonl", b"data1")
        await backend.write("workflows/2026-01-02/runs.jsonl", b"data2")
        await backend.write("events/2026-01-01/events.jsonl", b"data3")

        files = await backend.list_files("workflows")
        assert len(files) == 2
        assert all("workflows" in f for f in files)

    @pytest.mark.asyncio
    async def test_list_files_empty_prefix(self, tmp_path):
        """List files returns empty list for non-existent prefix."""
        backend = LocalArchiveBackend(str(tmp_path))
        files = await backend.list_files("nonexistent")
        assert files == []

    @pytest.mark.asyncio
    async def test_exists(self, tmp_path):
        """Check file existence."""
        backend = LocalArchiveBackend(str(tmp_path))
        assert await backend.exists("missing.jsonl") is False

        await backend.write("present.jsonl", b"data")
        assert await backend.exists("present.jsonl") is True

    @pytest.mark.asyncio
    async def test_nested_directories(self, tmp_path):
        """Writing to nested paths creates directories."""
        backend = LocalArchiveBackend(str(tmp_path))
        await backend.write("a/b/c/d/file.txt", b"deep")
        result = await backend.read("a/b/c/d/file.txt")
        assert result == b"deep"


# ===================================================================
# 3e: Archiver — ArchiveConfig
# ===================================================================


class TestArchiveConfig:
    def test_defaults(self):
        """Config has sensible defaults."""
        config = ArchiveConfig()
        assert config.backend == "local"
        assert config.base_path == "./archives"
        assert config.prompt_retention_days == 30
        assert config.workflow_retention_days == 90
        assert config.audit_retention_days == 365
        assert config.prompt_format == "jsonl"
        assert config.backup_enabled is True

    def test_custom_config(self):
        """Config accepts custom values."""
        config = ArchiveConfig(
            backend="s3",
            bucket="my-bucket",
            region="us-east-1",
            prompt_retention_days=7,
            workflow_retention_days=30,
            workflow_format="parquet",
            backup_enabled=False,
        )
        assert config.backend == "s3"
        assert config.bucket == "my-bucket"
        assert config.region == "us-east-1"
        assert config.prompt_retention_days == 7
        assert config.workflow_format == "parquet"
        assert config.backup_enabled is False


# ===================================================================
# 3e: Archiver — helpers
# ===================================================================


class TestArchiveHelpers:
    def test_to_jsonl(self):
        """JSONL serialization works."""
        records = [
            {"id": 1, "name": "first"},
            {"id": 2, "name": "second"},
        ]
        result = _to_jsonl(records)
        assert isinstance(result, bytes)
        lines = result.decode().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == 1
        assert json.loads(lines[1])["name"] == "second"

    def test_to_parquet_fallback(self):
        """Parquet falls back to JSONL without pyarrow."""
        records = [{"x": 1}, {"x": 2}]
        # _to_parquet will use pyarrow if available, or fall back to JSONL
        result = _to_parquet(records)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_to_jsonl_with_dates(self):
        """JSONL handles non-serializable types via default=str."""
        from datetime import datetime

        records = [{"ts": datetime(2026, 1, 1, 12, 0, 0)}]
        result = _to_jsonl(records)
        parsed = json.loads(result.decode().strip())
        assert "2026" in parsed["ts"]

    def test_to_jsonl_empty(self):
        """JSONL with empty records returns single newline."""
        result = _to_jsonl([])
        assert result == b"\n"


# ===================================================================
# 4a: Visualization — workflow_to_mermaid
# ===================================================================


class TestWorkflowVisualization:
    def test_sequential_mermaid(self):
        """Sequential workflow renders as Mermaid."""
        wf_def = {
            "type": "sequential",
            "steps": [
                {"type": "agent", "agent": "researcher"},
                {"type": "agent", "agent": "writer"},
            ],
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "researcher" in result
        assert "writer" in result
        assert "-->" in result

    def test_parallel_mermaid(self):
        """Parallel workflow renders fork/join when nested."""
        # workflow_to_mermaid treats top-level "steps" key as sequential.
        # To test parallel rendering, nest a parallel node inside a
        # sequential workflow as one of its steps.
        wf_def = {
            "steps": [
                {
                    "type": "parallel",
                    "steps": [
                        {"type": "agent", "agent": "agent_a"},
                        {"type": "agent", "agent": "agent_b"},
                    ],
                },
            ],
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "Fork" in result
        assert "Join" in result
        assert "agent_a" in result
        assert "agent_b" in result

    def test_conditional_mermaid(self):
        """Conditional renders diamond node."""
        wf_def = {
            "type": "conditional",
            "condition": {"contains": "error"},
            "then": {"agent": "error_handler"},
            "else": {"agent": "normal_handler"},
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "contains" in result
        assert "error" in result
        assert "yes" in result
        assert "no" in result

    def test_router_mermaid(self):
        """Router renders with route labels."""
        wf_def = {
            "type": "router",
            "prompt": "Classify the input",
            "routes": {
                "technical": {"type": "agent", "agent": "tech_agent"},
                "billing": {"type": "agent", "agent": "billing_agent"},
            },
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "technical" in result
        assert "billing" in result
        assert "tech_agent" in result
        assert "billing_agent" in result

    def test_execution_to_mermaid(self):
        """Execution detail renders with status colors."""

        @dataclass
        class StepDetail:
            step_name: str
            status: str
            duration_seconds: float | None = None

        @dataclass
        class ExecDetail:
            steps: list[StepDetail]

        execution = ExecDetail(
            steps=[
                StepDetail("research", "completed", 2.5),
                StepDetail("draft", "completed", 5.0),
                StepDetail("review", "failed", 1.2),
            ]
        )

        result = execution_to_mermaid(execution)
        assert "graph TD" in result
        assert "research" in result
        assert "2.5s" in result
        assert "draft" in result
        assert "5.0s" in result
        assert "review" in result
        # Check status styling
        assert "fill:#4caf50" in result  # completed = green
        assert "fill:#f44336" in result  # failed = red

    def test_from_yaml_string(self):
        """YAML string renders to Mermaid."""
        yaml_str = """
type: sequential
steps:
  - type: agent
    agent: step_one
  - type: agent
    agent: step_two
  - type: agent
    agent: step_three
"""
        result = workflow_to_mermaid_from_yaml(yaml_str)
        assert "graph TD" in result
        assert "step_one" in result
        assert "step_two" in result
        assert "step_three" in result

    def test_loop_mermaid(self):
        """Loop workflow renders correctly."""
        wf_def = {
            "type": "loop",
            "agent": "refiner",
            "max_iterations": 5,
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "Loop" in result
        assert "refiner" in result
        assert "max=5" in result
        assert "repeat" in result

    def test_approval_mermaid(self):
        """Approval node renders correctly."""
        wf_def = {
            "type": "approval",
            "prompt": "Review and approve",
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "Review and approve" in result

    def test_sequential_with_steps_key(self):
        """Workflow dict with 'steps' key at top level."""
        wf_def = {
            "steps": [
                {"type": "agent", "agent": "a"},
                {"type": "agent", "agent": "b"},
            ]
        }
        result = workflow_to_mermaid(wf_def)
        assert "graph TD" in result
        assert "-->" in result


# ===================================================================
# 4b: Progress Reporting
# ===================================================================


class TestProgressReporting:
    @pytest.mark.asyncio
    async def test_sequential_progress_callback(self):
        """DurableRunner calls progress callback."""
        store = InMemoryStore()
        progress_calls: list[tuple[int, int, str]] = []

        def on_progress(current: int, total: int, name: str) -> None:
            progress_calls.append((current, total, name))

        agents = [
            MockAgent(name="agent_a", response="output_a"),
            MockAgent(name="agent_b", response="output_b"),
            MockAgent(name="agent_c", response="output_c"),
        ]

        runner = DurableRunner(
            store=store,
            on_progress=on_progress,
        )

        result = await runner.run_sequential(
            agents=agents,
            input_text="start",
            run_id="progress-run-1",
        )

        assert result == "output_c"
        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3, "agent_a")
        assert progress_calls[1] == (2, 3, "agent_b")
        assert progress_calls[2] == (3, 3, "agent_c")

    @pytest.mark.asyncio
    async def test_loop_progress_callback(self):
        """Loop runner calls progress callback per iteration."""
        store = InMemoryStore()
        progress_calls: list[tuple[int, int, str]] = []

        def on_progress(current: int, total: int, name: str) -> None:
            progress_calls.append((current, total, name))

        agent = MockAgent(name="looper", response="iterated")
        runner = DurableRunner(
            store=store,
            on_progress=on_progress,
        )

        await runner.run_loop(
            agent=agent,
            input_text="start",
            max_iterations=3,
            run_id="loop-progress-1",
        )

        assert len(progress_calls) == 3
        assert progress_calls[0] == (1, 3, "looper")
        assert progress_calls[1] == (2, 3, "looper")
        assert progress_calls[2] == (3, 3, "looper")

    @pytest.mark.asyncio
    async def test_no_progress_callback(self):
        """DurableRunner works without progress callback."""
        store = InMemoryStore()
        agents = [MockAgent(name="a", response="done")]
        runner = DurableRunner(store=store)

        result = await runner.run_sequential(
            agents=agents,
            input_text="test",
            run_id="no-progress-1",
        )
        assert result == "done"

    @pytest.mark.asyncio
    async def test_loop_progress_with_early_stop(self):
        """Loop progress reports only for executed iterations."""
        store = InMemoryStore()
        progress_calls: list[tuple[int, int, str]] = []

        def on_progress(current: int, total: int, name: str) -> None:
            progress_calls.append((current, total, name))

        agent = MockAgent(name="stopper", response="DONE")

        def stop_after_two(result: str, iteration: int) -> bool:
            return iteration >= 1  # Stop after 2nd iteration

        runner = DurableRunner(
            store=store,
            on_progress=on_progress,
        )

        await runner.run_loop(
            agent=agent,
            input_text="go",
            max_iterations=10,
            should_stop=stop_after_two,
            run_id="early-stop-1",
        )

        assert len(progress_calls) == 2
        assert progress_calls[0] == (1, 10, "stopper")
        assert progress_calls[1] == (2, 10, "stopper")
