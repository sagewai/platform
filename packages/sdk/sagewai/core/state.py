# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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
from datetime import datetime
from enum import Enum
from typing import Any

from sagewai.artifacts.models import ArtifactDestination
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


class ExecutionMode(str, Enum):
    """Per-run execution mode (architecture's Mode 0/1/2/3/3b taxonomy).

    Maps to docs/architecture/execution-modes.md. The worker reads this
    field off the run row and dispatches accordingly. Per-step override
    is a follow-up — for now the run-level mode is what the worker sees.
    """

    BARE = "bare"           # Mode 0: inline on worker, no sandbox
    SANDBOXED = "sandboxed" # Mode 1: sandbox, no identity
    IDENTITY = "identity"   # Mode 2: sandbox + Sealed identity injected
    FULL = "full"           # Mode 3: + CLI agent + artifact destination
    FULL_JIT = "full_jit"   # Mode 3b: + bidirectional JIT credential callback


def sandbox_mode_for(execution_mode: ExecutionMode) -> SandboxMode:
    """Derive the legacy SandboxMode from an ExecutionMode.

    BARE → NONE; everything else → PER_RUN. Used at enqueue to keep the
    Plan 3a routing predicates (worker capability matching, capacity
    label projection) functional while ExecutionMode becomes the
    primary driver.
    """
    if execution_mode is ExecutionMode.BARE:
        return SandboxMode.NONE
    return SandboxMode.PER_RUN


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
    # Sealed-iii.C: per-step injection state for replay safety.
    # Captured at step completion; None for pre-iii.C runs and Mode 0 steps.
    injection_snapshot: Any = None  # InjectionSnapshot | None — Any avoids import cycle


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

    # ── execution mode (architecture Mode 0/1/2/3/3b) ──
    # First-class taxonomy from docs/architecture/execution-modes.md. Worker
    # dispatch reads this field. requires_sandbox_mode below is derived at
    # enqueue and kept for back-compat (Plan 3a routing predicates use it).
    execution_mode: ExecutionMode = ExecutionMode.BARE

    # ── sandbox requirements (Plan 3a) — resolved at enqueue, concrete on disk ──
    requires_sandbox_mode: SandboxMode = SandboxMode.NONE
    requires_image: str = field(
        default_factory=lambda: f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}"
    )
    requires_variant: SandboxImageVariant | None = None
    requires_network_policy: NetworkPolicy = NetworkPolicy.NONE

    # ── sealed-i cascade resolution (resolved at enqueue) ──
    security_profile_ref: str | None = None
    effective_env_keys: list[str] = field(default_factory=list)
    effective_secret_keys: list[str] = field(default_factory=list)

    # ── sealed-iii.A revocation (set on hard-revoke fan-out) ──
    revoked_at: datetime | None = None
    revoke_reason: str | None = None

    # ── plan ART (artifact destination) — resolved at enqueue ──
    # See docs/superpowers/specs/2026-04-27-plan-art-artifact-destination-design.md.
    # None = no upload (also covers all Mode 0/1/2 runs).
    artifact_destination: ArtifactDestination | None = None

    # ── sealed-iii.C replay (set when this run is a replay) ──
    replay_of_run_id: str | None = None
    replay_from_step: int | None = None
    code_hash: str | None = None

    # ── sealed-v reactive directives ──
    directive_chain: list[Any] = field(default_factory=list)  # list[DirectiveChainEntry]
    estimated_cost_usd: float | None = None
    replay_re_evaluate_directives: bool = False
    execution_mode_override: ExecutionMode | None = None
    identity_from: str | None = None  # "original_injection" | "current_cascade" | None

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
            "execution_mode": self.execution_mode.value,
            "requires_sandbox_mode": self.requires_sandbox_mode.value,
            "requires_image": self.requires_image,
            "requires_variant": (
                self.requires_variant.value if self.requires_variant else None
            ),
            "requires_network_policy": self.requires_network_policy.value,
            "security_profile_ref": self.security_profile_ref,
            "effective_env_keys": self.effective_env_keys,
            "effective_secret_keys": self.effective_secret_keys,
            "revoked_at": (self.revoked_at.isoformat() if self.revoked_at else None),
            "revoke_reason": self.revoke_reason,
            "artifact_destination": (
                self.artifact_destination.model_dump(mode="json")
                if self.artifact_destination
                else None
            ),
            "replay_of_run_id": self.replay_of_run_id,
            "replay_from_step": self.replay_from_step,
            "code_hash": self.code_hash,
            "directive_chain": [
                e.model_dump(mode="json") for e in self.directive_chain
            ],
            "estimated_cost_usd": self.estimated_cost_usd,
            "replay_re_evaluate_directives": self.replay_re_evaluate_directives,
            "execution_mode_override": (
                self.execution_mode_override.value
                if self.execution_mode_override
                else None
            ),
            "identity_from": self.identity_from,
            "steps": {
                name: {
                    "status": rec.status.value,
                    "result": rec.result,
                    "error": rec.error,
                    "attempts": rec.attempts,
                    "started_at": rec.started_at,
                    "completed_at": rec.completed_at,
                    "injection_snapshot": (
                        rec.injection_snapshot.model_dump()
                        if rec.injection_snapshot is not None
                        else None
                    ),
                }
                for name, rec in self.steps.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowRun:
        """Deserialize from a dictionary (inverse of to_dict)."""
        from sagewai.sealed.directives.models import DirectiveChainEntry
        from sagewai.sealed.replay import InjectionSnapshot

        chain_raw = data.get("directive_chain", []) or []
        directive_chain = [DirectiveChainEntry.model_validate(e) for e in chain_raw]

        emo_raw = data.get("execution_mode_override")
        execution_mode_override = (
            ExecutionMode(emo_raw) if emo_raw else None
        )

        steps = {}
        for name, s in data.get("steps", {}).items():
            snap_dict = s.get("injection_snapshot")
            snap = InjectionSnapshot.model_validate(snap_dict) if snap_dict else None
            steps[name] = StepRecord(
                step_name=name,
                status=StepStatus(s["status"]),
                result=s.get("result"),
                error=s.get("error"),
                attempts=s.get("attempts", 0),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
                injection_snapshot=snap,
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
            execution_mode=ExecutionMode(
                data.get("execution_mode", "bare")
            ),
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
            security_profile_ref=data.get("security_profile_ref"),
            effective_env_keys=data.get("effective_env_keys", []),
            effective_secret_keys=data.get("effective_secret_keys", []),
            revoked_at=(
                datetime.fromisoformat(data["revoked_at"])
                if data.get("revoked_at")
                else None
            ),
            revoke_reason=data.get("revoke_reason"),
            artifact_destination=(
                ArtifactDestination.model_validate(data["artifact_destination"])
                if data.get("artifact_destination")
                else None
            ),
            replay_of_run_id=data.get("replay_of_run_id"),
            replay_from_step=data.get("replay_from_step"),
            code_hash=data.get("code_hash"),
            directive_chain=directive_chain,
            estimated_cost_usd=data.get("estimated_cost_usd"),
            replay_re_evaluate_directives=bool(
                data.get("replay_re_evaluate_directives", False)
            ),
            execution_mode_override=execution_mode_override,
            identity_from=data.get("identity_from"),
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


# ---------------------------------------------------------------------------
# Process-wide configurable default workflow store
# ---------------------------------------------------------------------------

_default_store: WorkflowStore | None = None


def configure_default_workflow_store(store: WorkflowStore | None) -> None:
    """Set the process-wide default WorkflowStore for DurableWorkflow.

    Called by the admin serve lifespan to wire the SQLite-backed store
    (or PostgresStore when SAGEWAI_DATABASE_URL is set) as the default for
    any DurableWorkflow that does not supply its own ``store=`` argument.

    Tests never call this function, so they continue to receive
    InMemoryStore (the fallback when both ``store`` and ``_default_store``
    are None).

    Parameters
    ----------
    store:
        The WorkflowStore to use as the process-wide default, or ``None``
        to reset to InMemoryStore fallback (useful in teardown).
    """
    global _default_store
    _default_store = store


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


def _build_revocation_registry(store: Any) -> Any | None:
    """Construct a RevocationRegistry if the store supports it, else None.

    Best-effort: any failure returns None (registry-less behavior).
    Module-level so tests can monkeypatch.
    """
    try:
        from sagewai.sealed.audit import AuditWriter
        from sagewai.sealed.revocation import RevocationRegistry
        # The registry needs an asyncpg pool — check that the store exposes one
        if not hasattr(store, "_pool"):
            return None
        return RevocationRegistry(store, audit_writer=AuditWriter(store))
    except Exception:
        return None


async def _snapshot_secret_provenance(
    profile_id: str | None,
    secret_keys: list[str],
) -> tuple[dict[str, str], dict[str, str | None], dict[str, int]]:
    """Return (hashes, version_ids, active_revocation_ids) for the given keys.

    Best-effort: every failure path returns the still-usable empty/null
    triple. Replay-time decisions handle the implications.
    Module-level + async so tests can monkeypatch with an async replacement.
    """
    hashes: dict[str, str] = {}
    versions: dict[str, str | None] = {}
    revs: dict[str, int] = {}
    if not profile_id or not secret_keys:
        return hashes, versions, revs

    try:
        from sagewai.sealed.refs import ProfileRef, resolve_backend
        from sagewai.sealed.replay import hash_secret_value

        ref = ProfileRef.parse(profile_id)
        backend = resolve_backend(ref)
        profile = await backend.get_profile(ref.path)
        for k in secret_keys:
            v = profile.secrets.get(k)
            if v is not None:
                hashes[k] = hash_secret_value(v)
            # Builtin: no version history (Task 6); future per-backend
            # overrides can populate the version_id here.
            versions[k] = None
    except Exception:
        logger.debug(
            "snapshot provenance lookup skipped for profile=%s",
            profile_id, exc_info=True,
        )

    return hashes, versions, revs


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
        self._store = store or _default_store or InMemoryStore()
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
            from sagewai.sealed.replay import compute_code_hash
            wf_run = WorkflowRun(
                workflow_name=self.name,
                run_id=run_id,
                input_data=kwargs,
                started_at=time.time(),
                code_hash=compute_code_hash(self),
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

            # Mark RUNNING and save BEFORE execution.
            # Sealed-iii.C: preserve any seeded injection_snapshot from
            # replay_from() so the sandbox-acquire path can use it.
            seeded_snapshot = (
                existing.injection_snapshot
                if existing is not None
                else None
            )
            record = StepRecord(step_name=step_def.name, started_at=time.time())
            record.status = StepStatus.RUNNING
            record.injection_snapshot = seeded_snapshot
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

        # Sealed-iii.C: replay.completed audit on successful replay finish.
        if wf_run.replay_of_run_id is not None:
            try:
                from sagewai.sealed.audit import AuditWriter
                await AuditWriter(self._store).emit(
                    event_type="replay.completed",
                    run_id=wf_run.run_id,
                    project_id=wf_run.project_id,
                    profile_id=wf_run.security_profile_ref,
                    details={
                        "original_run_id": wf_run.replay_of_run_id,
                        "from_step": wf_run.replay_from_step,
                        "duration_seconds": (
                            wf_run.completed_at
                            - (wf_run.started_at or wf_run.completed_at)
                        ),
                    },
                )
            except Exception:
                logger.debug("replay.completed audit emit skipped", exc_info=True)

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

                # Sealed-iii.C: capture per-step injection snapshot.
                # Skipped for Mode 0 steps (no Sealed scope on the run).
                run = self._current_run
                if run is not None and (
                    run.effective_secret_keys or run.effective_env_keys
                ):
                    from sagewai.sealed.replay import InjectionSnapshot
                    hashes, versions, revs = await _snapshot_secret_provenance(
                        run.security_profile_ref,
                        list(run.effective_secret_keys),
                    )
                    record.injection_snapshot = InjectionSnapshot(
                        effective_env_keys=list(run.effective_env_keys),
                        effective_secret_keys=list(run.effective_secret_keys),
                        security_profile_ref=run.security_profile_ref,
                        secret_value_hashes=hashes,
                        secret_value_versions=versions,
                        revocations_active_at_step=revs,
                        captured_at=record.completed_at,
                    )

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

    async def _original_run_used_callbacks(self, original: WorkflowRun) -> bool:
        """Check whether the original run had Mode-3b callback requests.

        Test seam: production reads ``sealed_audit_events`` for
        ``credential.requested`` rows tagged with run_id; tests inject
        the ``signals['__test_callbacks_present__']`` sentinel.
        """
        if original.signals.get("__test_callbacks_present__"):
            return True
        try:
            if hasattr(self._store, "_pool"):
                row = await self._store._pool.fetchrow(
                    "SELECT 1 FROM sealed_audit_events "
                    "WHERE run_id = $1 AND event_type = 'credential.requested' "
                    "LIMIT 1",
                    original.run_id,
                )
                return row is not None
        except Exception:
            return False
        return False

    async def replay_from(
        self,
        original_run_id: str,
        *,
        from_step: int = 0,
        actor_id: str | None = None,
    ) -> str:
        """Create a replay run starting from step index ``from_step``.

        Steps 0..from_step-1 are copied from the original as COMPLETED.
        Steps from_step..end are PENDING with seeded injection snapshots.

        Raises:
            ValueError: from_step out of range, or original_run_id not found.
            LegacyRunNoSnapshotError: original predates Sealed-iii.C.
            WorkflowVersionMismatchError: workflow code shape changed.
            ModeNotReplayableError: mode can't be replayed (e.g., 3b w/ callback).
        """
        from sagewai.sealed.replay import (
            LegacyRunNoSnapshotError,
            ModeNotReplayableError,
            WorkflowVersionMismatchError,
            compute_code_hash,
        )

        original = await self._store.load_run(self.name, original_run_id)
        if original is None:
            raise ValueError(
                f"Run {original_run_id!r} not found for workflow {self.name!r}"
            )
        if not (0 <= from_step < len(self._steps)):
            raise ValueError(
                f"from_step={from_step} out of range "
                f"[0, {len(self._steps)}) for workflow {self.name!r}"
            )

        # Code-shape guard
        current_hash = compute_code_hash(self)
        if original.code_hash and original.code_hash != current_hash:
            raise WorkflowVersionMismatchError(
                run_id=original_run_id,
                original_hash=original.code_hash,
                current_hash=current_hash,
            )

        # Mode-3b callback guard
        if original.execution_mode is ExecutionMode.FULL_JIT:
            if await self._original_run_used_callbacks(original):
                raise ModeNotReplayableError(
                    run_id=original_run_id,
                    mode="full_jit",
                    reason=(
                        "original run had JIT credential callbacks; "
                        "Sealed-iv will add cached-callback replay"
                    ),
                )

        new_steps: dict[str, StepRecord] = {}
        for idx, step_def in enumerate(self._steps):
            orig_rec = original.steps.get(step_def.name)
            if idx < from_step:
                # Pre-replay step: copy as completed.
                # Legacy guard: a Sealed-using mode without a snapshot is
                # unreplayable — re-enqueue is the only safe path.
                if (
                    original.execution_mode is not ExecutionMode.BARE
                    and (orig_rec is None or orig_rec.injection_snapshot is None)
                ):
                    raise LegacyRunNoSnapshotError(
                        run_id=original_run_id,
                        step_name=step_def.name,
                    )
                new_steps[step_def.name] = StepRecord(
                    step_name=step_def.name,
                    status=StepStatus.COMPLETED,
                    result=orig_rec.result if orig_rec else None,
                    error=None,
                    attempts=orig_rec.attempts if orig_rec else 0,
                    started_at=orig_rec.started_at if orig_rec else None,
                    completed_at=orig_rec.completed_at if orig_rec else None,
                    injection_snapshot=(
                        orig_rec.injection_snapshot if orig_rec else None
                    ),
                )
            else:
                # Pending; carry the snapshot forward for replay-injection
                new_steps[step_def.name] = StepRecord(
                    step_name=step_def.name,
                    status=StepStatus.PENDING,
                    injection_snapshot=(
                        orig_rec.injection_snapshot if orig_rec else None
                    ),
                )

        new_run_id = hashlib.sha256(
            f"replay:{original_run_id}:{from_step}:{time.time_ns()}".encode()
        ).hexdigest()[:16]

        new_run = WorkflowRun(
            workflow_name=self.name,
            run_id=new_run_id,
            steps=new_steps,
            status=StepStatus.PENDING,
            input_data=original.input_data,
            started_at=time.time(),
            project_id=original.project_id,
            execution_mode=original.execution_mode,
            requires_sandbox_mode=original.requires_sandbox_mode,
            requires_image=original.requires_image,
            requires_variant=original.requires_variant,
            requires_network_policy=original.requires_network_policy,
            security_profile_ref=original.security_profile_ref,
            effective_env_keys=list(original.effective_env_keys),
            effective_secret_keys=list(original.effective_secret_keys),
            replay_of_run_id=original_run_id,
            replay_from_step=from_step,
            code_hash=current_hash,
        )
        await self._store.save_run(new_run)
        logger.info(
            "Workflow %s replay run %s of %s (from_step=%d, actor=%s)",
            self.name, new_run_id, original_run_id, from_step, actor_id,
        )

        # Sealed-iii.C: replay.started audit (best-effort).
        try:
            from sagewai.sealed.audit import AuditWriter
            await AuditWriter(self._store).emit(
                event_type="replay.started",
                run_id=new_run_id,
                project_id=original.project_id,
                profile_id=original.security_profile_ref,
                details={
                    "original_run_id": original_run_id,
                    "from_step": from_step,
                    "actor_id": actor_id,
                    "mode": original.execution_mode.value,
                    "code_hash": current_hash,
                },
            )
        except Exception:
            logger.debug("replay.started audit emit skipped", exc_info=True)

        return new_run_id

    async def enqueue(
        self,
        input_data: Any = None,
        *,
        execution_mode: ExecutionMode = ExecutionMode.BARE,
        requires_sandbox_mode: SandboxMode | None = None,
        requires_image: str | None = None,
        requires_network_policy: NetworkPolicy | None = None,
        security_profile_ref: str | None = None,
        security_overrides: dict[str, str] | None = None,
        artifact_destination: ArtifactDestination | None = None,
    ) -> str:
        """Create and persist a WorkflowRun with resolved sandbox requirements.

        Runs the cascade: explicit kwargs → agent spec → project defaults →
        SDK hard default. Returns the new run_id. The run is saved to the store
        in PENDING status for a worker to claim.

        Parameters
        ----------
        input_data:
            Arbitrary JSON-serialisable input passed to the first step.
        execution_mode:
            First-class execution mode (Mode 0/1/2/3/3b — see
            docs/architecture/execution-modes.md). Drives worker dispatch.
        requires_sandbox_mode:
            Explicit sandbox isolation level for this run. If omitted, derived
            from ``execution_mode`` (BARE → NONE; everything else → PER_RUN).
        requires_image:
            Explicit image reference (e.g. ``ghcr.io/sagewai/sandbox-ml:0.1.5``).
        requires_network_policy:
            Explicit network policy for this run.
        security_profile_ref:
            Optional Sealed-i profile reference for this run (user-level override).
        security_overrides:
            Optional per-key env overrides applied on top of the resolved profile.
        artifact_destination:
            Optional Plan ART artifact destination for this run (run-level
            override, beats workflow code default and admin override). Mode 3+
            only — set on Mode 0/1/2 runs and the upload step is skipped with
            an ``artifact.mode_mismatch`` audit warning.
        """
        from sagewai.artifacts.resolution import ArtifactDestinationLevels
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

        # Sealed-i cascade build
        sealed_levels = None
        audit_writer = None
        audit_context = None
        try:
            from sagewai.admin.state_file import AdminStateFile
            from sagewai.sealed.audit import AuditWriter
            from sagewai.sealed.resolution import CascadeLevel

            state = AdminStateFile()
            sealed_cfg = state.get_sealed_config()
            workflow_cfg = state.get_workflow_sealed_config(self.name) or {}

            code_profile_ref = getattr(type(self), "security_profile_ref", None)
            code_overrides = getattr(type(self), "security_overrides", None)

            sealed_levels = [
                CascadeLevel(
                    name="system",
                    profile_ref=sealed_cfg.get("system_profile_ref"),
                    overrides=sealed_cfg.get("system_overrides"),
                ),
                CascadeLevel(
                    name="workflow",
                    profile_ref=workflow_cfg.get("profile_ref") or code_profile_ref,
                    overrides=workflow_cfg.get("overrides") or code_overrides,
                ),
                CascadeLevel(
                    name="user",
                    profile_ref=security_profile_ref,
                    overrides=security_overrides,
                ),
            ]

            # Only emit audit if the cascade has anything to resolve
            if any(lv.profile_ref for lv in sealed_levels):
                audit_writer = AuditWriter(self._store)
                audit_context = {
                    "workflow_name": self.name,
                    "project_id": getattr(self, "project_id", None),
                }
            else:
                sealed_levels = None  # skip resolve_security_profile altogether
        except Exception as exc:
            # Sealed config is opt-in; if it can't be loaded (no admin state, etc.),
            # log + proceed without sealed resolution. Sandbox cascade still runs.
            logger.debug("Sealed cascade build skipped: %s", exc)
            sealed_levels = None

        revocation_registry = _build_revocation_registry(self._store)

        # Derive a default sandbox mode from execution_mode if the caller
        # didn't pin one explicitly. Keeps Plan 3a routing predicates functional.
        derived_mode = (
            requires_sandbox_mode
            if requires_sandbox_mode is not None
            else sandbox_mode_for(execution_mode)
        )

        # Plan ART cascade build — only resolve if at least one layer is set
        artifact_destination_levels: ArtifactDestinationLevels | None = None
        code_artifact_dest = getattr(type(self), "artifact_destination", None)
        admin_artifact_dest: ArtifactDestination | None = None
        try:
            from sagewai.admin.state_file import AdminStateFile

            admin_artifact_dest = AdminStateFile().get_workflow_artifact_destination(
                self.name,
                project_id=getattr(self, "project_id", None),
            )
        except Exception as exc:
            logger.debug("Artifact destination admin override skipped: %s", exc)
        if (
            code_artifact_dest is not None
            or admin_artifact_dest is not None
            or artifact_destination is not None
        ):
            artifact_destination_levels = ArtifactDestinationLevels(
                code_default=code_artifact_dest,
                admin_override=admin_artifact_dest,
                run_override=artifact_destination,
            )

        requirements = await resolve_requirements(
            explicit_mode=derived_mode,
            explicit_image=requires_image,
            explicit_network_policy=requires_network_policy,
            agent_requirements=agent_req,
            project_defaults=project_defaults,
            sealed_levels=sealed_levels,
            audit_writer=audit_writer,
            audit_context=audit_context,
            revocation_registry=revocation_registry,  # NEW in Sealed-iii.A
            artifact_destination_levels=artifact_destination_levels,
        )

        # Mode-mismatch warning: artifact destination set but run isn't Mode 3+.
        # Run still proceeds; the upload step will skip with an audit event.
        if requirements.artifact_destination is not None and execution_mode in (
            ExecutionMode.BARE,
            ExecutionMode.SANDBOXED,
            ExecutionMode.IDENTITY,
        ):
            logger.warning(
                "Artifact destination set on a non-Mode-3+ run "
                "(workflow=%s, execution_mode=%s) — upload will be skipped.",
                self.name,
                execution_mode.value,
            )
            if audit_writer is not None:
                try:
                    await audit_writer.emit(
                        event_type="artifact.mode_mismatch",
                        actor_type="system",
                        details={
                            "execution_mode": execution_mode.value,
                            "destination_type": (
                                requirements.artifact_destination.type.value
                            ),
                        },
                        context=audit_context,
                    )
                except Exception as exc:
                    logger.debug("artifact.mode_mismatch audit emit failed: %s", exc)

        from sagewai.sealed.replay import compute_code_hash
        code_hash_value = compute_code_hash(self)

        run_id = _generate_run_id(self.name, input_data or {})
        run = WorkflowRun(
            workflow_name=self.name,
            run_id=run_id,
            input_data=input_data,
            started_at=time.time(),
            execution_mode=execution_mode,
            requires_sandbox_mode=requirements.sandbox_mode,
            requires_image=requirements.image,
            requires_variant=requirements.variant,
            requires_network_policy=requirements.network_policy,
            security_profile_ref=requirements.security_profile_ref,
            effective_env_keys=list(requirements.effective_env_keys),
            effective_secret_keys=list(requirements.effective_secret_keys),
            artifact_destination=requirements.artifact_destination,
            code_hash=code_hash_value,
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
