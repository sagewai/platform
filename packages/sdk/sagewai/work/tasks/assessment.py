# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cycle assessment result models and merge policy."""

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


def merge_assessment(
    plan: AcceptedPlan,
    *,
    attempt_id: str,
    outcomes: Mapping[str, str],
    deterministic: Sequence[MatrixResult],
    assessor: TaskAssessmentResult,
) -> TaskAssessmentResult:
    """Section 11: the verifier judges deterministic items, the assessor judges the rest.

    A failing item or an unmet step forces ``replan`` however confident the assessor is; only
    the assessor can say ``blocked``, and only unanimous success can say ``accept``.
    """
    judged = {result.item_id: result for result in assessor.matrix_results}
    judged.update({result.item_id: result for result in deterministic})
    results = tuple(
        judged.get(item.id, MatrixResult(item_id=item.id, passed=False))
        for item in plan.acceptance_matrix
    )
    unmet = tuple(step for step in plan.steps if outcomes.get(step.id) != "accepted")
    gaps = (
        *assessor.gaps,
        *(
            AssessmentGap(
                statement=f"step {step.id} did not reach an accepted outcome",
                severity="high",
                suggested_step=step.id,
            )
            for step in unmet
        ),
    )
    if assessor.verdict == "blocked":
        verdict: Literal["accept", "replan", "blocked"] = "blocked"
    elif unmet or any(not result.passed for result in results):
        verdict = "replan"
    else:
        verdict = assessor.verdict
    return TaskAssessmentResult(
        attempt_id=attempt_id,
        matrix_results=results,
        gaps=gaps,
        verdict=verdict,
    )


__all__ = ["AssessmentGap", "MatrixResult", "TaskAssessmentResult", "merge_assessment"]
