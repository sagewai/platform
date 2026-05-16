# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-iii.C — per-step injection snapshots + replay path.

See docs/superpowers/specs/2026-04-27-sealed-iii-c-replay-design.md.
"""
from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sagewai.core.state import DurableWorkflow, ExecutionMode, WorkflowRun


class ReplayError(RuntimeError):
    """Base class for replay-failure exceptions."""


class LegacyRunNoSnapshotError(ReplayError):
    def __init__(self, run_id: str, step_name: str) -> None:
        super().__init__(
            f"Run {run_id!r} step {step_name!r} has no injection snapshot "
            f"(predates Sealed-iii.C); re-enqueue the workflow instead."
        )
        self.run_id = run_id
        self.step_name = step_name


class WorkflowVersionMismatchError(ReplayError):
    def __init__(self, run_id: str, original_hash: str, current_hash: str) -> None:
        super().__init__(
            f"Workflow code shape changed since run {run_id!r} "
            f"(original={original_hash[:12]}..., current={current_hash[:12]}...); "
            f"re-enqueue instead of replaying."
        )
        self.run_id = run_id
        self.original_hash = original_hash
        self.current_hash = current_hash


class RotationDriftError(ReplayError):
    def __init__(self, profile_id: str, secret_key: str) -> None:
        super().__init__(
            f"Secret {profile_id!r}/{secret_key!r} rotated since the original "
            f"run; backend has no value history available for this version."
        )
        self.profile_id = profile_id
        self.secret_key = secret_key


class ModeNotReplayableError(ReplayError):
    def __init__(self, run_id: str, mode: str, reason: str) -> None:
        super().__init__(
            f"Run {run_id!r} cannot be replayed (mode={mode}): {reason}"
        )
        self.run_id = run_id
        self.mode = mode
        self.reason = reason


class InjectionSnapshot(BaseModel):
    """Per-step record of injection state, captured at step completion."""

    model_config = ConfigDict(extra="forbid")

    effective_env_keys: list[str] = Field(default_factory=list)
    effective_secret_keys: list[str] = Field(default_factory=list)
    security_profile_ref: str | None = None
    secret_value_hashes: dict[str, str] = Field(default_factory=dict)
    secret_value_versions: dict[str, str | None] = Field(default_factory=dict)
    revocations_active_at_step: dict[str, int] = Field(default_factory=dict)
    captured_at: float


def compute_code_hash(workflow: DurableWorkflow) -> str:
    """SHA-256 over '|'.join(workflow.step_names).

    Stable per step-shape; changes only when steps are added, removed,
    renamed, or reordered. Granular enough to catch shape drift; weak
    enough that pure step-body refactors don't invalidate replays.
    """
    payload = "|".join(workflow.step_names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hash_secret_value(value: str) -> str:
    """SHA-256 of a secret value as hex. Used for rotation detection."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def enqueue_replay(
    *,
    store: Any,
    original_run_id: str,
    from_step: int = 0,
    execution_mode_override: ExecutionMode | None = None,
    identity_from: str | None = None,
    security_profile_ref: str | None = None,
    re_evaluate_directives: bool = False,
) -> WorkflowRun:
    """Low-level replay-enqueue used by directive actions (PromoteRunMode,
    RestartWithFreshIdentity) that need to drive a replay at a different
    execution mode or with a freshly-resolved identity cascade.

    This is intentionally a thin wrapper: it loads the original run,
    builds a minimal :class:`~sagewai.core.state.WorkflowRun` carrying the
    two new override fields, persists it via ``store.save_run``, and returns
    it.  Heavy step-copy logic (i.e. copying completed steps) lives in
    :meth:`~sagewai.core.state.DurableWorkflow.replay_from`; callers that
    want that full machinery should use that method directly.

    The ``store`` object must expose:

    * ``async load_run(run_id: str) -> WorkflowRun``
    * ``async save_run(run: WorkflowRun) -> None``

    Parameters
    ----------
    store:
        Persistence backend (duck-typed).
    original_run_id:
        The run being replayed.
    from_step:
        First step index to re-execute (0-based).
    execution_mode_override:
        When set, the new run's ``execution_mode`` is this value; the field
        ``execution_mode_override`` is also set for audit / directive tracking.
        When ``None``, the original run's ``execution_mode`` is preserved.
    identity_from:
        ``"current_cascade"`` — ``SealedSecretProvider.replay_env_for`` will
        re-resolve the cascade instead of reading the historical snapshot.
        ``"original_injection"`` or ``None`` — default: read the snapshot.
    security_profile_ref:
        Override the security profile for the new run.  When ``None``, the
        original run's ``security_profile_ref`` is inherited.
    re_evaluate_directives:
        Whether the replay executor should re-evaluate reactive directives for
        completed steps (forwarded to ``replay_re_evaluate_directives``).
    """
    from sagewai.core.state import WorkflowRun

    original: WorkflowRun = await store.load_run(original_run_id)

    effective_mode = execution_mode_override if execution_mode_override is not None else original.execution_mode
    effective_security_profile = (
        security_profile_ref if security_profile_ref is not None
        else original.security_profile_ref
    )

    new_run_id = hashlib.sha256(
        f"replay:{original_run_id}:{from_step}:{time.time_ns()}".encode()
    ).hexdigest()[:16]

    new_run = WorkflowRun(
        workflow_name=original.workflow_name,
        run_id=new_run_id,
        execution_mode=effective_mode,
        execution_mode_override=execution_mode_override,
        identity_from=identity_from,
        security_profile_ref=effective_security_profile,
        effective_env_keys=list(original.effective_env_keys),
        effective_secret_keys=list(original.effective_secret_keys),
        replay_of_run_id=original_run_id,
        replay_from_step=from_step,
        replay_re_evaluate_directives=re_evaluate_directives,
        project_id=original.project_id,
        input_data=original.input_data,
        started_at=time.time(),
    )
    await store.save_run(new_run)
    return new_run
