# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure validation and folding for criterion-linked Work completion."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sagewai.work.contract import AcceptanceCriterion, WorkContract
from sagewai.work.models import (
    CompletionEvaluation,
    CriterionVerification,
    VerificationResult,
    WorkItem,
)


def validate_criterion_subset(
    contract: WorkContract,
    criterion_ids: tuple[str, ...],
) -> tuple[AcceptanceCriterion, ...]:
    """Resolve one non-empty, unique subset from the accepted contract."""
    if not criterion_ids:
        raise ValueError("criterion ids cannot be empty")
    if len(set(criterion_ids)) != len(criterion_ids):
        raise ValueError("criterion ids must be unique")

    criteria_by_id = {criterion.id: criterion for criterion in contract.acceptance_criteria}
    unknown = tuple(
        criterion_id for criterion_id in criterion_ids if criterion_id not in criteria_by_id
    )
    if unknown:
        raise ValueError(f"unknown criterion ids: {', '.join(unknown)}")
    return tuple(criteria_by_id[criterion_id] for criterion_id in criterion_ids)


def validate_verification_result(
    contract: WorkContract,
    criterion_ids: tuple[str, ...],
    result: VerificationResult,
) -> VerificationResult:
    """Require a verification result to cover exactly its requested subset."""
    requested = validate_criterion_subset(contract, criterion_ids)
    if result.project_id != contract.project_id:
        raise ValueError("verification result belongs to a different project")
    if result.contract_id != contract.id:
        raise ValueError("verification result belongs to a different contract")

    requested_ids = {criterion.id for criterion in requested}
    result_ids = {criterion.criterion_id for criterion in result.criterion_results}
    if result_ids != requested_ids:
        raise ValueError("verification result does not match requested criterion ids")
    return result


def fold_verification_results(
    contract: WorkContract,
    results: Iterable[VerificationResult],
) -> tuple[CriterionVerification, ...]:
    """Fold ordered durable results, keeping the latest value per current criterion."""
    current: dict[str, CriterionVerification] = {}
    for result in results:
        if result.contract_id != contract.id:
            continue
        criterion_ids = tuple(item.criterion_id for item in result.criterion_results)
        validate_verification_result(contract, criterion_ids, result)
        for criterion_result in result.criterion_results:
            current[criterion_result.criterion_id] = criterion_result

    return tuple(
        current[criterion.id]
        for criterion in contract.acceptance_criteria
        if criterion.id in current
    )


def evaluate_completion(
    *,
    work: WorkItem,
    contract: WorkContract,
    verification_results: Iterable[VerificationResult],
    evaluated_at: datetime,
) -> CompletionEvaluation:
    """Evaluate exactly the latest accepted contract, rejecting incomplete proof."""
    if work.project_id != contract.project_id:
        raise ValueError("work belongs to a different project")
    if work.id != contract.work_id:
        raise ValueError("contract belongs to different work")

    criterion_results = fold_verification_results(contract, verification_results)
    current_ids = {result.criterion_id for result in criterion_results}
    missing = tuple(
        criterion.id
        for criterion in contract.acceptance_criteria
        if criterion.id not in current_ids
    )
    if missing:
        raise ValueError(f"missing criterion ids: {', '.join(missing)}")

    return CompletionEvaluation(
        project_id=contract.project_id,
        work_id=work.id,
        contract_id=contract.id,
        passed=all(result.passed for result in criterion_results),
        criterion_results=criterion_results,
        evaluated_at=evaluated_at,
    )
