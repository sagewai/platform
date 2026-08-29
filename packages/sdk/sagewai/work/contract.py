# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Versioned Work contract model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceCriterion(BaseModel):
    """One stable, independently verifiable contract obligation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    project_id: str | None
    statement: str = Field(min_length=1)
    verification_kind: Literal["deterministic", "profile", "policy"]


class WorkContract(BaseModel):
    """Immutable boundary between requested intent and execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None
    work_id: str
    version: int = Field(ge=1)
    goal: str
    allowed_scope: tuple[str, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(min_length=1)
    constraints: tuple[str, ...]
    non_goals: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    assumption_ids: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    design_required: bool
    profile_context: dict[str, Any] = Field(default_factory=dict)
    supersedes: str | None = None

    @model_validator(mode="after")
    def validate_acceptance_criteria(self) -> WorkContract:
        criterion_ids: set[str] = set()
        for criterion in self.acceptance_criteria:
            if criterion.project_id != self.project_id:
                raise ValueError("criterion belongs to a different project")
            if criterion.id in criterion_ids:
                raise ValueError("acceptance criterion ids must be unique")
            criterion_ids.add(criterion.id)
        return self
