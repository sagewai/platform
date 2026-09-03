# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic assessment of one cycle (spec section 11, deterministic half)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.tasks.plan import AcceptedPlan


class MatrixResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    passed: bool
    evidence_refs: tuple[str, ...] = ()


class AssessmentGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    severity: Literal["low", "medium", "high"]
    suggested_step: str


class TaskAssessmentResult(BaseModel):
    """The section 11 result schema; the assessor stage produces the same shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str = Field(min_length=1)
    matrix_results: tuple[MatrixResult, ...] = ()
    gaps: tuple[AssessmentGap, ...] = ()
    verdict: Literal["accept", "replan", "blocked"]


def assess_cycle(
    plan: AcceptedPlan,
    *,
    attempt_id: str,
    outcomes: Mapping[str, str],
    evidence: Sequence[str],
) -> TaskAssessmentResult:
    """Judge the cycle from its step outcomes alone.

    Every matrix item is satisfied by the step Works: a deterministic item's command is the
    target's locked verification command, which each step Work already ran before its
    repository outcome was accepted. Judging an assessment item on its own evidence needs
    the read-only assessor stage at the merged head, which is not in this increment.
    """
    unmet = tuple(step for step in plan.steps if outcomes.get(step.id) != "accepted")
    passed = not unmet
    refs = tuple(evidence)
    return TaskAssessmentResult(
        attempt_id=attempt_id,
        matrix_results=tuple(
            MatrixResult(item_id=item.id, passed=passed, evidence_refs=refs)
            for item in plan.acceptance_matrix
        ),
        gaps=tuple(
            AssessmentGap(
                statement=f"step {step.id} did not reach an accepted outcome",
                severity="high",
                suggested_step=step.id,
            )
            for step in unmet
        ),
        verdict="accept" if passed else "replan",
    )


__all__ = ["AssessmentGap", "MatrixResult", "TaskAssessmentResult", "assess_cycle"]
