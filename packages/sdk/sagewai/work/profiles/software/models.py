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

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.artifacts.models import ArtifactRef
from sagewai.work.contract import WorkContract
from sagewai.work.models import Assumption, ReviewFinding, VerificationResult


class SoftwareRepositoryOutcome(str, Enum):
    """Repository boundary that the accepted software contract requires."""

    VERIFIED_COMMIT = "verified_commit"
    PULL_REQUEST = "pull_request"
    MERGED = "merged"


class SoftwareDeliveryContractContext(BaseModel):
    """Explicit optional delivery outcome owned by one project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    target_environment: str = Field(min_length=1, pattern=r"\S")
    criterion_ids: tuple[str, ...] = Field(min_length=1)
    release_provider_ref: str = Field(min_length=1, pattern=r"\S")
    deployment_provider_ref: str = Field(min_length=1, pattern=r"\S")
    observation_provider_ref: str = Field(min_length=1, pattern=r"\S")
    rollout_policy_ref: str = Field(min_length=1, pattern=r"\S")
    rollback_policy_ref: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def _unique_criterion_ids(self) -> SoftwareDeliveryContractContext:
        if len(set(self.criterion_ids)) != len(self.criterion_ids):
            raise ValueError("delivery criterion ids must be unique")
        return self


class SoftwareContractContext(BaseModel):
    """Software-specific immutable contract state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    base_sha: str
    repository_outcome: SoftwareRepositoryOutcome
    repository_criterion_id: str
    delivery: SoftwareDeliveryContractContext | None = None

    @model_validator(mode="after")
    def _validate_delivery_boundary(self) -> SoftwareContractContext:
        if self.delivery is None:
            return self
        if self.repository_outcome is not SoftwareRepositoryOutcome.MERGED:
            raise ValueError("delivery requires a merged repository outcome")
        if self.delivery.project_id != self.project_id:
            raise ValueError("delivery context belongs to a different project")
        if self.repository_criterion_id in self.delivery.criterion_ids:
            raise ValueError("repository criterion cannot be a delivery criterion")
        return self

    def validate_contract(self, contract: WorkContract) -> None:
        """Validate profile-owned outcomes against one accepted contract."""
        if self.project_id != contract.project_id:
            raise ValueError("software context belongs to a different project")
        criterion_ids = {criterion.id for criterion in contract.acceptance_criteria}
        if self.repository_criterion_id not in criterion_ids:
            raise ValueError("repository criterion is not in the accepted contract")
        if self.delivery is not None:
            unknown_ids = set(self.delivery.criterion_ids) - criterion_ids
            if unknown_ids:
                raise ValueError("delivery criterion is not in the accepted contract")


class SoftwareCapsuleContext(BaseModel):
    """Software-specific context compiled for an operator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None

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

    project_id: str | None

    base_sha: str
    result_sha: str | None


class SoftwareVerificationCheck(BaseModel):
    """One deterministic software verification command receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None

    name: str
    command: str
    exit_code: int
    artifact_ref: str | None


class SoftwareReviewFindingContext(BaseModel):
    """Optional source location attached to a generic review finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None

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
