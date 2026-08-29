# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for exact criterion-linked Work completion."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work import (
    AcceptanceCriterion,
    CompletionEvaluation,
    CriterionVerification,
    VerificationResult,
    WorkContract,
    WorkItem,
    evaluate_completion,
    fold_verification_results,
    validate_criterion_subset,
    validate_verification_result,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _criterion(
    criterion_id: str,
    *,
    project_id: str | None = "project-a",
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=criterion_id,
        project_id=project_id,
        statement=f"Prove {criterion_id}",
        verification_kind="deterministic",
    )


def _contract(
    *,
    contract_id: str = "contract-1",
    project_id: str | None = "project-a",
) -> WorkContract:
    return WorkContract(
        id=contract_id,
        project_id=project_id,
        work_id="work-1",
        version=1,
        goal="Complete every accepted obligation",
        allowed_scope=("packages/sdk/sagewai/work",),
        acceptance_criteria=(
            _criterion("implementation", project_id=project_id),
            _criterion("review", project_id=project_id),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
    )


def _work(*, project_id: str | None = "project-a") -> WorkItem:
    return WorkItem(
        id="work-1",
        project_id=project_id,
        profile="software",
        source="local",
        source_ref=None,
        title="Completion",
        description="Prove the contract",
        created_at=NOW,
    )


def _criterion_result(
    criterion_id: str,
    *,
    passed: bool = True,
    contract_id: str = "contract-1",
    project_id: str | None = "project-a",
    evidence_ref: str | None = None,
) -> CriterionVerification:
    return CriterionVerification(
        project_id=project_id,
        contract_id=contract_id,
        criterion_id=criterion_id,
        passed=passed,
        evidence_refs=(evidence_ref or f"evidence://{criterion_id}",),
    )


def _verification(
    *criterion_results: CriterionVerification,
    attempt_id: str = "attempt-1",
    contract_id: str = "contract-1",
    project_id: str | None = "project-a",
) -> VerificationResult:
    return VerificationResult(
        project_id=project_id,
        contract_id=contract_id,
        attempt_id=attempt_id,
        stage="verification",
        passed=all(result.passed for result in criterion_results),
        criterion_results=criterion_results,
        evidence_refs=tuple(ref for result in criterion_results for ref in result.evidence_refs),
    )


def test_contract_rejects_duplicate_cross_project_or_legacy_criteria() -> None:
    values = _contract().model_dump()
    values["acceptance_criteria"] = (_criterion("same"), _criterion("same"))
    with pytest.raises(ValidationError, match="criterion ids must be unique"):
        WorkContract.model_validate(values)

    values["acceptance_criteria"] = ("legacy string criterion",)
    with pytest.raises(ValidationError):
        WorkContract.model_validate(values)
    for field in ("id", "statement"):
        criterion = _criterion("non-empty").model_dump()
        criterion[field] = ""
        with pytest.raises(ValidationError):
            AcceptanceCriterion.model_validate(criterion)

    values = _contract().model_dump()
    values["acceptance_criteria"] = (
        _criterion("implementation"),
        _criterion("review", project_id="project-b"),
    )
    with pytest.raises(ValidationError, match="criterion belongs to a different project"):
        WorkContract.model_validate(values)


def test_verification_result_rejects_nested_ownership_and_inconsistent_passed() -> None:
    with pytest.raises(ValidationError, match="criterion belongs to a different contract"):
        _verification(_criterion_result("implementation", contract_id="contract-old"))

    with pytest.raises(ValidationError, match="criterion result ids must be unique"):
        _verification(
            _criterion_result("implementation"),
            _criterion_result("implementation"),
        )

    with pytest.raises(ValidationError, match="criterion belongs to a different project"):
        _verification(_criterion_result("implementation", project_id="project-b"))

    with pytest.raises(ValidationError, match="verification passed is inconsistent"):
        VerificationResult(
            project_id="project-a",
            contract_id="contract-1",
            attempt_id="attempt-1",
            stage="verification",
            passed=True,
            criterion_results=(_criterion_result("implementation", passed=False),),
            evidence_refs=("evidence://implementation",),
        )


def test_passing_criterion_requires_evidence_but_failed_criterion_does_not() -> None:
    with pytest.raises(ValidationError, match="passing criterion requires evidence"):
        CriterionVerification(
            project_id="project-a",
            contract_id="contract-1",
            criterion_id="implementation",
            passed=True,
            evidence_refs=(),
        )

    failed = CriterionVerification(
        project_id="project-a",
        contract_id="contract-1",
        criterion_id="implementation",
        passed=False,
        evidence_refs=(),
    )
    assert failed.evidence_refs == ()


def test_completion_evaluation_rejects_inconsistent_aggregate_verdict() -> None:
    with pytest.raises(ValidationError, match="completion passed is inconsistent"):
        CompletionEvaluation(
            project_id="project-a",
            work_id="work-1",
            contract_id="contract-1",
            passed=True,
            criterion_results=(_criterion_result("implementation", passed=False),),
            evaluated_at=NOW,
        )


def test_validate_verification_result_requires_the_exact_requested_subset() -> None:
    contract = _contract()
    result = _verification(_criterion_result("implementation"))

    assert validate_criterion_subset(contract, ("implementation",)) == (
        contract.acceptance_criteria[0],
    )
    assert validate_verification_result(contract, ("implementation",), result) is result

    with pytest.raises(ValueError, match="must be unique"):
        validate_criterion_subset(contract, ("implementation", "implementation"))
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_criterion_subset(contract, ())
    with pytest.raises(ValueError, match="unknown criterion ids"):
        validate_criterion_subset(contract, ("unknown",))
    with pytest.raises(ValueError, match="does not match requested criterion ids"):
        validate_verification_result(contract, ("review",), result)


def test_fold_replaces_a_criterion_only_with_a_later_result_for_same_contract() -> None:
    contract = _contract()
    failed = _verification(
        _criterion_result("implementation", passed=False),
        attempt_id="attempt-1",
    )
    repaired = _verification(
        _criterion_result("implementation", evidence_ref="evidence://repair"),
        attempt_id="attempt-2",
    )
    reviewed = _verification(
        _criterion_result("review"),
        attempt_id="attempt-3",
    )

    folded = fold_verification_results(contract, (failed, repaired, reviewed))

    assert folded == (
        repaired.criterion_results[0],
        reviewed.criterion_results[0],
    )


def test_fold_ignores_stale_contract_results_but_rejects_current_scope_mismatch() -> None:
    stale = _verification(
        _criterion_result("implementation", contract_id="contract-old"),
        contract_id="contract-old",
    )

    assert fold_verification_results(_contract(), (stale,)) == ()
    with pytest.raises(ValueError, match="missing criterion ids"):
        evaluate_completion(
            work=_work(),
            contract=_contract(),
            verification_results=(stale,),
            evaluated_at=NOW,
        )

    wrong_project = _verification(
        _criterion_result("implementation", project_id="project-b"),
        project_id="project-b",
    )
    with pytest.raises(ValueError, match="different project"):
        fold_verification_results(_contract(), (wrong_project,))


def test_completion_requires_exact_current_contract_set() -> None:
    contract = _contract()
    implementation = _verification(_criterion_result("implementation"))
    review = _verification(_criterion_result("review"), attempt_id="attempt-2")

    evaluation = evaluate_completion(
        work=_work(),
        contract=contract,
        verification_results=(implementation, review),
        evaluated_at=NOW,
    )

    assert evaluation.passed is True
    assert evaluation.criterion_results == (
        implementation.criterion_results[0],
        review.criterion_results[0],
    )

    with pytest.raises(ValueError, match="missing criterion ids: review"):
        evaluate_completion(
            work=_work(),
            contract=contract,
            verification_results=(implementation,),
            evaluated_at=NOW,
        )


def test_failed_exact_set_produces_non_passing_evaluation() -> None:
    failed = _verification(_criterion_result("implementation", passed=False))
    review = _verification(_criterion_result("review"), attempt_id="attempt-2")

    evaluation = evaluate_completion(
        work=_work(),
        contract=_contract(),
        verification_results=(failed, review),
        evaluated_at=NOW,
    )

    assert evaluation.passed is False


def test_completion_rejects_work_or_result_from_another_scope() -> None:
    contract = _contract()
    results = (
        _verification(_criterion_result("implementation")),
        _verification(_criterion_result("review"), attempt_id="attempt-2"),
    )

    with pytest.raises(ValueError, match="work belongs to a different project"):
        evaluate_completion(
            work=_work(project_id="project-b"),
            contract=contract,
            verification_results=results,
            evaluated_at=NOW,
        )


def test_completion_models_are_frozen() -> None:
    criterion = _criterion("implementation")
    result = _criterion_result("implementation")

    with pytest.raises(ValidationError):
        criterion.statement = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.passed = False  # type: ignore[misc]
