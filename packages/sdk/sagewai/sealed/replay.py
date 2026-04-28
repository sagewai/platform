# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed-iii.C — per-step injection snapshots + replay path.

See docs/superpowers/specs/2026-04-27-sealed-iii-c-replay-design.md.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sagewai.core.state import DurableWorkflow


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
