# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Durable state — checkpoint and recovery for agent workflows.

Provides decorators and utilities for making agent pipelines durable:
step-level checkpointing, automatic retry on failure, and recovery
from the last successful checkpoint.

Usage::

    from sagewai.core.state import DurableWorkflow
    from sagewai.core.stores import PostgresStore

    store = PostgresStore(database_url="postgresql://localhost/sagewai")
    await store.initialize()

    wf = DurableWorkflow(name="article-pipeline", store=store)

    @wf.step("research")
    async def research(topic: str) -> str:
        return await researcher.chat(topic)

    @wf.step("draft", retries=3)
    async def draft(research_output: str) -> str:
        return await writer.chat(research_output)

    result = await wf.run(topic="quantum computing")
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sagewai.errors import SagewaiWorkflowError
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    """Status of a workflow step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    WAITING = "waiting"


@dataclass
class StepRecord:
    """Record of a single step execution."""

    step_name: str
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str | None = None
    attempts: int = 0
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class WorkflowRun:
    """A single workflow execution run."""

    workflow_name: str
    run_id: str
    steps: dict[str, StepRecord] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    input_data: Any = None
    output_data: Any = None
    started_at: float | None = None
    completed_at: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None

    # ── sandbox requirements (Plan 3a) — resolved at enqueue, concrete on disk ──
    requires_sandbox_mode: SandboxMode = SandboxMode.NONE
    requires_image: str = field(
        default_factory=lambda: f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}"
    )
    requires_variant: SandboxImageVariant | None = None
    requires_network_policy: NetworkPolicy = NetworkPolicy.NONE

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return {
            "workflow_name": self.workflow_name,
            "run_id": self.run_id,
            "status": self.status.value,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "signals": self.signals,
            "project_id": self.project_id,
            "requires_sandbox_mode": self.requires_sandbox_mode.value,
            "requires_image": self.requires_image,
            "requires_variant": (
                self.requires_variant.value if self.requires_variant else None
            ),
            "requires_network_policy": self.requires_network_policy.value,
            "steps": {
                name: {
                    "status": rec.status.value,
                    "result": rec.result,
                    "error": rec.error,
                    "attempts": rec.attempts,
                    "started_at": rec.started_at,
                    "completed_at": rec.completed_at,
                }
                for name, rec in self.steps.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowRun:
        """Deserialize from a dictionary (inverse of to_dict)."""
        steps = {}
        for name, s in data.get("steps", {}).items():
            steps[name] = StepRecord(
                step_name=name,
                status=StepStatus(s["status"]),
                result=s.get("result"),
                error=s.get("error"),
                attempts=s.get("attempts", 0),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
            )
        variant_val = data.get("requires_variant")
        return cls(
            workflow_name=data["workflow_name"],
            run_id=data["run_id"],
            steps=steps,
            status=StepStatus(data.get("status", "pending")),
            input_data=data.get("input_data"),
            output_data=data.get("output_data"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            signals=data.get("signals", {}),
            project_id=data.get("project_id"),
            requires_sandbox_mode=SandboxMode(
                data.get("requires_sandbox_mode", "none")
            ),
            requires_image=data.get(
                "requires_image",
                f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}",
            ),
            requires_variant=(
                SandboxImageVariant(variant_val) if variant_val else None
            ),
            requires_network_policy=NetworkPolicy(
                data.get("requires_network_policy", "none")
            ),
        )


class WorkflowStore:
    """Protocol for workflow state persistence.

    Implementations store workflow runs and step results.
    The default InMemoryStore is for testing; swap with PostgresStore for production.
    """

    async def save_run(self, run: WorkflowRun) -> None:
        """Persist the workflow run state."""
        raise NotImplementedError

    async def load_run(self, workflow_name: str, run_id: str) -> WorkflowRun | None:
        """Load a workflow run by ID. Returns None if not found."""
        raise NotImplementedError

    async def list_runs(
        self, workflow_name: str, status: StepStatus | None = None
    ) -> list[WorkflowRun]:
        """List runs for a workflow, optionally filtered by status."""
        raise NotImplementedError

    async def recover_stale_runs(self, stale_timeout_seconds: int = 300) -> list[WorkflowRun]:
        """Find RUNNING workflows that haven't been updated within the timeout."""
        return []

    async def heartbeat(self, workflow_name: str, run_id: str) -> None:
        """Refresh updated_at to prevent stale detection."""
        pass


class InMemoryStore(WorkflowStore):
    """In-memory workflow store for testing and development."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._updated_at: dict[str, float] = {}

    async def save_run(self, run: WorkflowRun) -> None:
        key = f"{run.workflow_name}:{run.run_id}"
        self._runs[key] = run
        self._updated_at[key] = time.time()

    async def load_run(self, workflow_name: str, run_id: str) -> WorkflowRun | None:
        return self._runs.get(f"{workflow_name}:{run_id}")

    async def list_runs(
        self, workflow_name: str, status: StepStatus | None = None
    ) -> list[WorkflowRun]:
        runs = [r for r in self._runs.values() if r.workflow_name == workflow_name]
        if status is not None:
            runs = [r for r in runs if r.status == status]
        return runs

    async def recover_stale_runs(self, stale_timeout_seconds: int = 300) -> list[WorkflowRun]:
        """Find RUNNING workflows that haven't been updated within the timeout."""
        cutoff = time.time() - stale_timeout_seconds
        return [
            r
            for key, r in self._runs.items()
            if r.status == StepStatus.RUNNING and self._updated_at.get(key, 0) < cutoff
        ]

    async def heartbeat(self, workflow_name: str, run_id: str) -> None:
        """Refresh updated_at to prevent stale detection."""
        key = f"{workflow_name}:{run_id}"
        if key in self._runs:
            self._updated_at[key] = time.time()


@dataclass
class _StepDef:
    """Internal definition of a workflow step."""

    name: str
    fn: Callable[..., Awaitable[Any]]
    retries: int = 0
    retry_delay: float = 1.0
    timeout: float | None = None


def _generate_run_id(workflow_name: str, input_data: Any) -> str:
    """Generate a deterministic run ID from workflow name and input."""
    content = f"{workflow_name}:{json.dumps(input_data, sort_keys=True, default=str)}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class DurableWorkflow:
    """Durable workflow with step-level checkpointing and recovery.

    Steps are registered with the ``step`` decorator and executed in order.
    If a step fails, the workflow can be resumed from the last checkpoint.

    Parameters
    ----------
    name:
        Workflow name (used as storage key).
    store:
        Workflow state store. Defaults to InMemoryStore.
    """

    def __init__(
        self,
        name: str,
        *,
        store: WorkflowStore | None = None,
    ) -> None:
        self.name = name
        self._store = store or InMemoryStore()
        self._steps: list[_StepDef] = []
        self._current_run: WorkflowRun | None = None

    def step(
        self,
        name: str,
        *,
        retries: int = 0,
        retry_delay: float = 1.0,
        timeout: float | None = None,
    ) -> Callable:
        """Register a step in the workflow.

        Args:
            name: Step name (must be unique within the workflow).
            retries: Number of retry attempts on failure.
            retry_delay: Delay in seconds between retries.
            timeout: Optional timeout in seconds for the step.
        """

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self._steps.append(
                _StepDef(
                    name=name,
                    fn=fn,
                    retries=retries,
                    retry_delay=retry_delay,
                    timeout=timeout,
                )
            )
            return fn

        return decorator

    async def run(self, run_id: str | None = None, **kwargs: Any) -> Any:
        """Execute the workflow, resuming from checkpoint if available.

        Args:
            run_id: Optional run ID for resuming. Auto-generated if not provided.
            **kwargs: Input arguments passed to the first step.

        Returns:
            Output from the last step.
        """
        if run_id is None:
            run_id = _generate_run_id(self.name, kwargs)

        # Try to resume from checkpoint
        wf_run = await self._store.load_run(self.name, run_id)
        if wf_run is None:
            wf_run = WorkflowRun(
                workflow_name=self.name,
                run_id=run_id,
                input_data=kwargs,
                started_at=time.time(),
            )

        wf_run.status = StepStatus.RUNNING
        self._current_run = wf_run
        await self._store.save_run(wf_run)
        logger.info("Workflow %s run %s → RUNNING", self.name, run_id)

        current_input = kwargs
        last_output: Any = None

        for step_def in self._steps:
            # Check if step already completed (resuming)
            existing = wf_run.steps.get(step_def.name)
            if existing and existing.status == StepStatus.COMPLETED:
                logger.info(
                    "Skipping completed step %s (resuming workflow %s)",
                    step_def.name,
                    self.name,
                )
                last_output = existing.result
                # Use completed step result as next input
                if isinstance(last_output, str):
                    current_input = {list(kwargs.keys())[0]: last_output} if kwargs else {}
                continue

            # Mark RUNNING and save BEFORE execution
            record = StepRecord(step_name=step_def.name, started_at=time.time())
            record.status = StepStatus.RUNNING
            wf_run.steps[step_def.name] = record
            await self._store.save_run(wf_run)

            try:
                last_output = await self._execute_step(step_def, record, current_input)
            except WorkflowWaiting:
                record.status = StepStatus.WAITING
                wf_run.status = StepStatus.WAITING
                await self._store.save_run(wf_run)
                self._current_run = None
                raise

            await self._store.save_run(wf_run)

            if record.status == StepStatus.FAILED:
                wf_run.status = StepStatus.FAILED
                await self._store.save_run(wf_run)
                self._current_run = None
                raise WorkflowStepError(
                    f"Step '{step_def.name}' failed after {record.attempts} attempts: "
                    f"{record.error}",
                    step_name=step_def.name,
                    workflow_name=self.name,
                    run_id=run_id,
                )

            # Pipe output to next step
            if isinstance(last_output, str):
                current_input = {list(kwargs.keys())[0]: last_output} if kwargs else {}

        wf_run.status = StepStatus.COMPLETED
        wf_run.output_data = last_output
        wf_run.completed_at = time.time()
        self._current_run = None
        await self._store.save_run(wf_run)
        logger.info(
            "Workflow %s run %s → COMPLETED (%.1fs)",
            self.name,
            run_id,
            wf_run.completed_at - (wf_run.started_at or wf_run.completed_at),
        )

        return last_output

    async def _execute_step(
        self, step_def: _StepDef, record: StepRecord, input_data: dict[str, Any]
    ) -> Any:
        """Execute a single step with retry and timeout logic."""
        max_attempts = step_def.retries + 1

        for attempt in range(max_attempts):
            record.attempts = attempt + 1
            record.status = StepStatus.RUNNING if attempt == 0 else StepStatus.RETRYING
            record.started_at = time.time()

            try:
                if step_def.timeout:
                    result = await asyncio.wait_for(
                        step_def.fn(**input_data),
                        timeout=step_def.timeout,
                    )
                else:
                    result = await step_def.fn(**input_data)

                record.status = StepStatus.COMPLETED
                record.result = result
                record.completed_at = time.time()
                logger.info(
                    "Step '%s' completed (attempt %d/%d)",
                    step_def.name,
                    attempt + 1,
                    max_attempts,
                )
                return result

            except WorkflowWaiting:
                raise
            except Exception as exc:
                record.error = str(exc)
                logger.warning(
                    "Step '%s' failed (attempt %d/%d): %s",
                    step_def.name,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(step_def.retry_delay)

        record.status = StepStatus.FAILED
        record.completed_at = time.time()
        return None

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by ID."""
        return await self._store.load_run(self.name, run_id)

    async def list_runs(self, status: StepStatus | None = None) -> list[WorkflowRun]:
        """List workflow runs, optionally filtered by status."""
        return await self._store.list_runs(self.name, status)

    def get_signal(self, signal_name: str) -> Any | None:
        """Check if signal data exists (called inside a step)."""
        if self._current_run is None:
            return None
        return self._current_run.signals.get(signal_name)

    async def signal(self, run_id: str, signal_name: str, data: Any) -> None:
        """Inject external data into a waiting workflow."""
        wf_run = await self._store.load_run(self.name, run_id)
        if wf_run is None:
            raise ValueError(f"No run found: {self.name}:{run_id}")
        wf_run.signals[signal_name] = data
        # Mark waiting steps as pending so they re-execute on resume
        for step in wf_run.steps.values():
            if step.status == StepStatus.WAITING:
                step.status = StepStatus.PENDING
        wf_run.status = StepStatus.PENDING
        await self._store.save_run(wf_run)

    @property
    def step_names(self) -> list[str]:
        """List registered step names in order."""
        return [s.name for s in self._steps]

    async def enqueue(
        self,
        input_data: Any = None,
        *,
        requires_sandbox_mode: SandboxMode | None = None,
        requires_image: str | None = None,
        requires_network_policy: NetworkPolicy | None = None,
    ) -> str:
        """Create and persist a WorkflowRun with resolved sandbox requirements.

        Runs the cascade: explicit kwargs → agent spec → project defaults →
        SDK hard default. Returns the new run_id. The run is saved to the store
        in PENDING status for a worker to claim.

        Parameters
        ----------
        input_data:
            Arbitrary JSON-serialisable input passed to the first step.
        requires_sandbox_mode:
            Explicit sandbox isolation level for this run.
        requires_image:
            Explicit image reference (e.g. ``ghcr.io/sagewai/sandbox-ml:0.1.5``).
        requires_network_policy:
            Explicit network policy for this run.
        """
        from sagewai.sandbox.resolution import resolve_requirements

        project_defaults = None
        if hasattr(self._store, "get_project_defaults"):
            try:
                project_defaults = await self._store.get_project_defaults(
                    getattr(self, "project_id", None)
                )
            except Exception:
                project_defaults = None

        agent_req = getattr(self, "_agent_requirements", None)

        requirements = resolve_requirements(
            explicit_mode=requires_sandbox_mode,
            explicit_image=requires_image,
            explicit_network_policy=requires_network_policy,
            agent_requirements=agent_req,
            project_defaults=project_defaults,
        )

        run_id = _generate_run_id(self.name, input_data or {})
        run = WorkflowRun(
            workflow_name=self.name,
            run_id=run_id,
            input_data=input_data,
            started_at=time.time(),
            requires_sandbox_mode=requirements.sandbox_mode,
            requires_image=requirements.image,
            requires_variant=requirements.variant,
            requires_network_policy=requirements.network_policy,
        )
        await self._store.save_run(run)
        logger.info(
            "Workflow %s enqueued run %s (mode=%s)",
            self.name,
            run_id,
            requirements.sandbox_mode.value,
        )
        return run_id


class WorkflowWaiting(Exception):  # noqa: N818
    """Raised by a step to park the workflow until an external signal arrives."""

    def __init__(self, signal_name: str) -> None:
        super().__init__(f"Workflow waiting for signal: {signal_name}")
        self.signal_name = signal_name


class WorkflowStepError(SagewaiWorkflowError):
    """Raised when a workflow step fails after all retries."""

    def __init__(
        self,
        message: str,
        *,
        step_name: str,
        workflow_name: str,
        run_id: str,
    ) -> None:
        super().__init__(message)
        self.step_name = step_name
        self.workflow_name = workflow_name
        self.run_id = run_id


class ApprovalRequest:
    """Data describing what needs approval."""

    def __init__(
        self,
        prompt: str,
        *,
        context: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        auto_approve_after: float | None = None,
    ) -> None:
        self.prompt = prompt
        self.context = context or {}
        self.timeout_seconds = timeout_seconds
        self.auto_approve_after = auto_approve_after


class ApprovalGate:
    """Human-in-the-loop approval gate for durable workflows.

    When a step encounters an approval gate, the workflow pauses
    (raises WorkflowWaiting) until a human approves or rejects.

    Usage in a DurableWorkflow step::

        gate = ApprovalGate(workflow=wf)

        @wf.step("review")
        async def review(content: str) -> str:
            await gate.request_approval(
                prompt=f"Approve publication of: {content[:100]}...",
                context={"content": content},
            )
            # This line only runs after approval
            return content

    From the admin/CLI::

        await gate.approve(run_id="run-123")
        # or
        await gate.reject(run_id="run-123", reason="Content needs revision")
    """

    SIGNAL_NAME = "__approval__"

    def __init__(self, workflow: DurableWorkflow) -> None:
        self._workflow = workflow

    async def request_approval(
        self,
        prompt: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request human approval. Blocks until approved or rejected.

        Raises WorkflowWaiting to park the workflow. On resume (after
        signal injection), checks the approval result.

        Returns the approval data (including any reviewer comments).
        Raises ApprovalDeniedError if rejected.
        """
        # Check if approval signal already exists (resuming after approval)
        signal_data = self._workflow.get_signal(self.SIGNAL_NAME)
        if signal_data is not None:
            if signal_data.get("approved"):
                return signal_data
            raise ApprovalDeniedError(
                signal_data.get("reason", "Approval denied"),
                reviewer=signal_data.get("reviewer"),
            )

        # No signal yet — park the workflow
        raise WorkflowWaiting(self.SIGNAL_NAME)

    async def approve(
        self,
        run_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
    ) -> None:
        """Approve a waiting workflow run."""
        await self._workflow.signal(
            run_id,
            self.SIGNAL_NAME,
            {
                "approved": True,
                "reviewer": reviewer,
                "comment": comment,
                "timestamp": time.time(),
            },
        )

    async def reject(
        self,
        run_id: str,
        *,
        reason: str = "",
        reviewer: str = "",
    ) -> None:
        """Reject a waiting workflow run."""
        await self._workflow.signal(
            run_id,
            self.SIGNAL_NAME,
            {
                "approved": False,
                "reason": reason,
                "reviewer": reviewer,
                "timestamp": time.time(),
            },
        )


class QueueFullError(SagewaiWorkflowError):
    """Raised when workflow queue exceeds its depth limit."""

    def __init__(self, current_depth: int, max_depth: int) -> None:
        super().__init__(
            f"Queue full: {current_depth}/{max_depth} pending runs"
        )
        self.current_depth = current_depth
        self.max_depth = max_depth


class ApprovalDeniedError(Exception):
    """Raised when a workflow approval is denied."""

    def __init__(self, reason: str, *, reviewer: str = "") -> None:
        super().__init__(f"Approval denied: {reason}")
        self.reason = reason
        self.reviewer = reviewer
