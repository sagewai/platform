# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cycle assessment merges verifier and assessor verdicts."""

from __future__ import annotations

import pytest

from sagewai.work.models import ProposedAcceptanceCriterion
from sagewai.work.tasks.assessment import MatrixResult, TaskAssessmentResult, merge_assessment
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
            MatrixItem(id="m2", statement="the change reads well", verification_kind="policy"),
        ),
    )


def _assessor(verdict="accept", *, passed=True):
    return TaskAssessmentResult(
        attempt_id="assessor",
        matrix_results=(MatrixResult(item_id="m2", passed=passed, evidence_refs=("assessor://m2",)),),
        verdict=verdict,
    )


def test_every_step_accepted_yields_accept_with_judged_items() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="task-1:assess:1",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True, evidence_refs=("git://" + "c" * 40,)),),
        assessor=_assessor(),
    )
    assert isinstance(result, TaskAssessmentResult)
    assert result.verdict == "accept"
    assert [item.item_id for item in result.matrix_results] == ["m1", "m2"]
    assert all(item.passed for item in result.matrix_results)
    assert result.matrix_results[0].evidence_refs == ("git://" + "c" * 40,)
    assert result.matrix_results[1].evidence_refs == ("assessor://m2",)
    assert result.gaps == ()


def test_an_unaccepted_step_yields_replan_with_one_gap_per_step() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="task-1:assess:1",
        outcomes={"s1": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True),),
        assessor=_assessor(),
    )
    assert result.verdict == "replan"
    assert [gap.suggested_step for gap in result.gaps] == ["s2"]
    assert result.gaps[0].severity == "high"
    assert [item.passed for item in result.matrix_results] == [True, True]


def test_the_result_round_trips_through_json() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True),),
        assessor=_assessor(),
    )
    assert TaskAssessmentResult.model_validate(result.model_dump(mode="json")) == result


def test_an_empty_attempt_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        merge_assessment(
            _plan(),
            attempt_id="",
            outcomes={},
            deterministic=(),
            assessor=_assessor(),
        )


def test_the_assessment_is_deterministic_and_preserves_evidence_per_item() -> None:
    evidence = ("git://" + "c" * 40, "git://" + "d" * 40)
    first = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True, evidence_refs=evidence),),
        assessor=_assessor(),
    )
    second = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True, evidence_refs=evidence),),
        assessor=_assessor(),
    )
    assert first == second
    assert first.matrix_results[0].evidence_refs == evidence


def test_every_unaccepted_step_becomes_a_gap_in_plan_order() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "failed"},
        deterministic=(MatrixResult(item_id="m1", passed=True),),
        assessor=_assessor(),
    )
    assert result.verdict == "replan"
    assert [gap.suggested_step for gap in result.gaps] == ["s1", "s2"]


def test_an_unjudged_item_fails() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(),
        assessor=TaskAssessmentResult(attempt_id="assessor", verdict="accept"),
    )
    assert result.verdict == "replan"
    assert {item.item_id: item.passed for item in result.matrix_results} == {"m1": False, "m2": False}


def test_a_blocked_assessor_wins_over_passing_items() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=True),),
        assessor=_assessor("blocked"),
    )
    assert result.verdict == "blocked"


def test_failing_deterministic_item_downgrades_accept_to_replan() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=False),),
        assessor=_assessor(),
    )
    assert result.verdict == "replan"


def test_deterministic_result_overrides_the_assessor_on_the_same_item() -> None:
    result = merge_assessment(
        _plan(),
        attempt_id="a",
        outcomes={"s1": "accepted", "s2": "accepted"},
        deterministic=(MatrixResult(item_id="m1", passed=False, evidence_refs=("git://failed",)),),
        assessor=TaskAssessmentResult(
            attempt_id="assessor",
            matrix_results=(
                MatrixResult(item_id="m1", passed=True, evidence_refs=("assessor://claimed",)),
                MatrixResult(item_id="m2", passed=True, evidence_refs=("assessor://m2",)),
            ),
            verdict="accept",
        ),
    )

    assert result.verdict == "replan"
    assert result.matrix_results[0] == MatrixResult(
        item_id="m1", passed=False, evidence_refs=("git://failed",)
    )
