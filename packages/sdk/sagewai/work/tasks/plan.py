# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plan result schema and deterministic acceptance."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.work.models import ClassifiedClaim, ProposedAcceptanceCriterion
from sagewai.work.tasks.intake import ClarificationQuestion
from sagewai.work.tasks.models import Budget, ReportTarget, SoftwareTarget


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    allowed_scope: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[ProposedAcceptanceCriterion, ...] = Field(min_length=1)
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    risk: Literal["low", "medium", "high"]
    design_required: bool = False
    depends_on: tuple[str, ...] = ()
    domain: Literal["ui", "backend", "data", "docs", "report"]
    size: Literal["s", "m", "l"] = "m"


class MatrixItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    verification_kind: Literal["deterministic", "assessment"]
    command: str | None = None

    @model_validator(mode="after")
    def _command_only_for_deterministic(self) -> MatrixItem:
        if self.verification_kind == "deterministic" and not self.command:
            raise ValueError("deterministic matrix items name a command")
        if self.verification_kind == "assessment" and self.command is not None:
            raise ValueError("assessment matrix items carry no command")
        return self


class TaskPlanResult(BaseModel):
    """Structured output of the planning stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str = Field(min_length=1)
    steps: tuple[PlanStep, ...] = ()
    acceptance_matrix: tuple[MatrixItem, ...] = ()
    clarifications: tuple[ClarificationQuestion, ...] = ()
    claims: tuple[ClassifiedClaim, ...] = ()

    @model_validator(mode="after")
    def _steps_xor_clarifications(self) -> TaskPlanResult:
        if self.clarifications and self.steps:
            raise ValueError("a plan result asks clarifications or proposes steps, not both")
        if not self.clarifications and not self.steps:
            raise ValueError("a plan result needs steps or clarifications")
        return self

    @property
    def asks_first(self) -> bool:
        return bool(self.clarifications)


class AcceptedPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    acceptance_matrix: tuple[MatrixItem, ...] = Field(min_length=1)


class PlanRejectedError(ValueError):
    """The planner's result violates a deterministic acceptance rule."""


def _is_surgical(target: str) -> bool:
    path = PurePosixPath(target.strip())
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def _topological(steps: tuple[PlanStep, ...]) -> tuple[PlanStep, ...]:
    by_id = {step.id: step for step in steps}
    if len(by_id) != len(steps):
        raise PlanRejectedError("duplicate step id")
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in by_id:
                raise PlanRejectedError(f"unknown dependency {dependency!r} in step {step.id!r}")
    ordered: list[PlanStep] = []
    state: dict[str, int] = {}

    def visit(step_id: str, trail: tuple[str, ...]) -> None:
        mark = state.get(step_id, 0)
        if mark == 2:
            return
        if mark == 1:
            raise PlanRejectedError(f"dependency cycle through {' -> '.join((*trail, step_id))}")
        state[step_id] = 1
        for dependency in by_id[step_id].depends_on:
            visit(dependency, (*trail, step_id))
        state[step_id] = 2
        ordered.append(by_id[step_id])

    for step in steps:
        visit(step.id, ())
    return tuple(ordered)


def accept_plan(
    result: TaskPlanResult,
    *,
    budget: Budget,
    target: SoftwareTarget | ReportTarget,
    version: int,
) -> AcceptedPlan:
    """Apply the spec §7 acceptance rules; raise PlanRejectedError with the reason."""
    if result.clarifications:
        raise PlanRejectedError("result asks clarifications instead of proposing steps")
    if len(result.steps) > budget.max_works_per_cycle:
        raise PlanRejectedError(
            f"{len(result.steps)} steps exceed the budget of {budget.max_works_per_cycle} works per cycle"
        )
    for step in result.steps:
        for scope in step.allowed_scope:
            if not _is_surgical(scope):
                raise PlanRejectedError(f"scope {scope!r} in step {step.id!r} is not surgical")
    ordered = _topological(result.steps)
    if not result.acceptance_matrix:
        raise PlanRejectedError("acceptance matrix is empty")
    matrix_ids = [item.id for item in result.acceptance_matrix]
    if len(set(matrix_ids)) != len(matrix_ids):
        raise PlanRejectedError("duplicate matrix item id")
    deterministic = [item for item in result.acceptance_matrix if item.verification_kind == "deterministic"]
    if isinstance(target, SoftwareTarget):
        if not deterministic:
            raise PlanRejectedError("software targets need at least one deterministic matrix item")
        for item in deterministic:
            if item.command not in target.verification_commands:
                raise PlanRejectedError(
                    f"matrix item {item.id!r} names {item.command!r}, not one of the locked verification commands"
                )
    elif deterministic:
        raise PlanRejectedError("report targets verify through the profile; matrix items must be assessments")
    return AcceptedPlan(version=version, steps=ordered, acceptance_matrix=result.acceptance_matrix)


__all__ = ["AcceptedPlan", "MatrixItem", "PlanRejectedError", "PlanStep", "TaskPlanResult", "accept_plan"]
