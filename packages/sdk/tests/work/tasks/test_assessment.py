# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic cycle assessment produces the section 11 result shape."""

from __future__ import annotations

import pytest

from sagewai.work.models import ProposedAcceptanceCriterion
from sagewai.work.tasks.assessment import TaskAssessmentResult, assess_cycle
from sagewai.work.tasks.plan import AcceptedPlan, MatrixItem, PlanStep

CRITERION = ProposedAcceptanceCriterion(statement="the suite passes", verification_kind="deterministic")


def _plan() -> AcceptedPlan:
    return AcceptedPlan(
        version=1,
        steps=(
            PlanStep(
                id="s1",
                title="one",
                goal="one",
                allowed_scope=("src",),
                acceptance_criteria=(CRITERION,),
                risk="low",
                domain="backend",
            ),
            PlanStep(
                id="s2",
                title="two",
                goal="two",
                allowed_scope=("src",),
                acceptance_criteria=(CRITERION,),
                risk="low",
                domain="backend",
                depends_on=("s1",),
            ),
        ),
        acceptance_matrix=(
            MatrixItem(
                id="m1",
                statement="just smoke passes",
                verification_kind="deterministic",
                command="just smoke",
            ),
            MatrixItem(id="m2", statement="the change reads well", verification_kind="assessment"),
        ),
    )


def test_every_step_accepted_yields_accept_with_evidence() -> None:
    result = assess_cycle(
        _plan(),
        attempt_id="task-1:assess:1",
        outcomes={"s1": "accepted", "s2": "accepted"},
        evidence=("git://" + "c" * 40,),
    )
    assert isinstance(result, TaskAssessmentResult)
    assert result.verdict == "accept"
    assert [item.item_id for item in result.matrix_results] == ["m1", "m2"]
    assert all(item.passed for item in result.matrix_results)
    assert result.matrix_results[0].evidence_refs == ("git://" + "c" * 40,)
    assert result.gaps == ()


def test_an_unaccepted_step_yields_replan_with_one_gap_per_step() -> None:
    result = assess_cycle(
        _plan(), attempt_id="task-1:assess:1", outcomes={"s1": "accepted"}, evidence=()
    )
    assert result.verdict == "replan"
    assert [gap.suggested_step for gap in result.gaps] == ["s2"]
    assert result.gaps[0].severity == "high"
    assert not any(item.passed for item in result.matrix_results)


def test_the_result_round_trips_through_json() -> None:
    result = assess_cycle(_plan(), attempt_id="a", outcomes={"s1": "accepted", "s2": "accepted"}, evidence=())
    assert TaskAssessmentResult.model_validate(result.model_dump(mode="json")) == result


def test_an_empty_attempt_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        assess_cycle(_plan(), attempt_id="", outcomes={}, evidence=())


def test_the_assessment_is_deterministic_and_carries_evidence_on_every_item() -> None:
    evidence = ("git://" + "c" * 40, "git://" + "d" * 40)
    first = assess_cycle(_plan(), attempt_id="a", outcomes={"s1": "accepted", "s2": "accepted"}, evidence=evidence)
    second = assess_cycle(_plan(), attempt_id="a", outcomes={"s1": "accepted", "s2": "accepted"}, evidence=evidence)
    assert first == second
    assert all(item.evidence_refs == evidence for item in first.matrix_results)


def test_every_unaccepted_step_becomes_a_gap_in_plan_order() -> None:
    result = assess_cycle(_plan(), attempt_id="a", outcomes={"s1": "failed"}, evidence=())
    assert result.verdict == "replan"
    assert [gap.suggested_step for gap in result.gaps] == ["s1", "s2"]


def test_the_model_accepts_the_blocked_verdict_of_the_assessor_stage() -> None:
    result = TaskAssessmentResult(attempt_id="a", verdict="blocked")
    assert result.verdict == "blocked"

