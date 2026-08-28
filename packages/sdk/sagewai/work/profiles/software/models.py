# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Typed context and workspace models for the software Work profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sagewai.artifacts.models import ArtifactRef
from sagewai.work.models import Assumption, ReviewFinding, VerificationResult


class SoftwareContractContext(BaseModel):
    """Software-specific immutable contract state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str


class SoftwareCapsuleContext(BaseModel):
    """Software-specific context compiled for an operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    current_sha: str
    repo_instructions: tuple[str, ...]
    verification_commands: tuple[str, ...]


class SoftwareAnalysisContext(BaseModel):
    """Canonical inputs and required result contract for software analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    software: SoftwareCapsuleContext
    analysis_result_schema: dict[str, Any]


class SoftwareDesignContext(BaseModel):
    """Canonical inputs and required result contract for software design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    software: SoftwareCapsuleContext
    design_result_schema: dict[str, Any]


class SoftwareAttemptContext(BaseModel):
    """Software-specific execution receipt state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str
    result_sha: str | None


class SoftwareVerificationCheck(BaseModel):
    """One deterministic software verification command receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    command: str
    exit_code: int
    artifact_ref: str | None


class SoftwareReviewFindingContext(BaseModel):
    """Optional source location attached to a generic review finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str | None
    line: int | None


class SoftwareReviewContext(BaseModel):
    """Canonical context compiled for an independent software review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    software: SoftwareCapsuleContext
    diff: str | None
    diff_artifact: ArtifactRef
    diff_workspace_path: str | None
    verification: VerificationResult
    relevant_files: tuple[str, ...]
    open_assumptions: tuple[Assumption, ...]
    review_result_schema: dict[str, Any]


class SoftwareDeliveryTriageContext(BaseModel):
    """Canonical failed-delivery evidence supplied to software repair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: str
    observation: dict[str, Any]
    summary: str
    evidence_refs: tuple[str, ...]


class SoftwareRepairContext(BaseModel):
    """Canonical context compiled for one bounded software repair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    software: SoftwareCapsuleContext
    diff: str | None
    diff_artifact: ArtifactRef
    diff_workspace_path: str | None
    verification: VerificationResult
    relevant_files: tuple[str, ...]
    open_assumptions: tuple[Assumption, ...]
    findings: tuple[ReviewFinding, ...]
    triage: SoftwareDeliveryTriageContext | None = None


class SoftwareWorkspace(BaseModel):
    """One isolated worktree pinned to an attempt's base revision."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    ref: str
    project_id: str
    work_id: str
    attempt_id: str
    repository: Path
    path: Path
    base_sha: str
    initial_sha: str


class WorkspaceStaleError(RuntimeError):
    """The pinned workspace no longer has the expected Git state."""
