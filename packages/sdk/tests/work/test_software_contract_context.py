# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Typed software repository and optional delivery contract boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sagewai.work import AcceptanceCriterion, WorkContract
from sagewai.work.profiles.software import (
    SoftwareContractContext,
    SoftwareDeliveryContractContext,
    SoftwareRepositoryOutcome,
)


def _contract(*criterion_ids: str, project_id: str | None = "project-a") -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id=project_id,
        work_id="work-1",
        version=1,
        goal="produce the accepted repository outcome",
        allowed_scope=("packages/sdk",),
        acceptance_criteria=tuple(
            AcceptanceCriterion(
                id=criterion_id,
                project_id=project_id,
                statement=f"satisfy {criterion_id}",
                verification_kind="profile",
            )
            for criterion_id in criterion_ids
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
    )


def _delivery(
    *criterion_ids: str,
    project_id: str | None = "project-a",
) -> SoftwareDeliveryContractContext:
    return SoftwareDeliveryContractContext(
        project_id=project_id,
        target_environment="accepted-target",
        criterion_ids=criterion_ids,
        release_provider_ref="provider://release",
        deployment_provider_ref="provider://deployment",
        observation_provider_ref="provider://observation",
        rollout_policy_ref="policy://rollout",
        rollback_policy_ref="policy://rollback",
    )


def test_repository_outcomes_are_explicit_and_stable() -> None:
    assert {item.value for item in SoftwareRepositoryOutcome} == {
        "verified_commit",
        "pull_request",
        "merged",
    }


def test_execution_route_requires_matching_fleet_organization() -> None:
    local = SoftwareContractContext(
        project_id="project-a",
        base_sha="a" * 40,
        repository_outcome=SoftwareRepositoryOutcome.VERIFIED_COMMIT,
        repository_criterion_id="repository",
        execution_route="local",
    )
    fleet = local.model_copy(update={"execution_route": "fleet", "fleet_org_id": "org-a"})

    assert local.fleet_org_id is None
    assert fleet.fleet_org_id == "org-a"
    with pytest.raises(ValidationError, match="fleet execution requires an organization"):
        SoftwareContractContext.model_validate({**local.model_dump(), "execution_route": "fleet"})
    with pytest.raises(ValidationError, match="local execution cannot name a Fleet organization"):
        SoftwareContractContext.model_validate({**local.model_dump(), "fleet_org_id": "org-a"})


def test_contract_context_accepts_known_disjoint_repository_and_delivery_criteria() -> None:
    context = SoftwareContractContext(
        project_id="project-a",
        base_sha="a" * 40,
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        repository_criterion_id="repository",
        delivery=_delivery("delivery"),
    )

    context.validate_contract(_contract("repository", "delivery"))


@pytest.mark.parametrize(
    ("context", "contract", "message"),
    [
        (
            SoftwareContractContext(
                project_id="project-a",
                base_sha="a" * 40,
                repository_outcome=SoftwareRepositoryOutcome.VERIFIED_COMMIT,
                repository_criterion_id="unknown",
            ),
            _contract("repository"),
            "repository criterion is not in the accepted contract",
        ),
        (
            SoftwareContractContext(
                project_id="project-b",
                base_sha="a" * 40,
                repository_outcome=SoftwareRepositoryOutcome.VERIFIED_COMMIT,
                repository_criterion_id="repository",
            ),
            _contract("repository"),
            "software context belongs to a different project",
        ),
        (
            SoftwareContractContext(
                project_id="project-a",
                base_sha="a" * 40,
                repository_outcome=SoftwareRepositoryOutcome.MERGED,
                repository_criterion_id="repository",
                delivery=_delivery("unknown"),
            ),
            _contract("repository", "delivery"),
            "delivery criterion is not in the accepted contract",
        ),
    ],
)
def test_contract_context_rejects_unknown_or_cross_project_criteria(
    context: SoftwareContractContext,
    contract: WorkContract,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        context.validate_contract(contract)


@pytest.mark.parametrize(
    "field",
    (
        "target_environment",
        "release_provider_ref",
        "deployment_provider_ref",
        "observation_provider_ref",
        "rollout_policy_ref",
        "rollback_policy_ref",
    ),
)
@pytest.mark.parametrize("value", ("", " \t "))
def test_delivery_target_provider_and_policy_refs_must_be_non_blank(
    field: str,
    value: str,
) -> None:
    payload = _delivery("delivery").model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        SoftwareDeliveryContractContext.model_validate(payload)


def test_delivery_criteria_must_be_non_empty_unique_and_disjoint() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        _delivery()
    with pytest.raises(ValidationError, match="delivery criterion ids must be unique"):
        _delivery("delivery", "delivery")
    with pytest.raises(
        ValidationError, match="repository criterion cannot be a delivery criterion"
    ):
        SoftwareContractContext(
            project_id="project-a",
            base_sha="a" * 40,
            repository_outcome=SoftwareRepositoryOutcome.MERGED,
            repository_criterion_id="repository",
            delivery=_delivery("repository"),
        )


def test_delivery_requires_merged_repository_outcome_and_exact_project() -> None:
    with pytest.raises(ValidationError, match="delivery requires a merged repository outcome"):
        SoftwareContractContext(
            project_id="project-a",
            base_sha="a" * 40,
            repository_outcome=SoftwareRepositoryOutcome.PULL_REQUEST,
            repository_criterion_id="repository",
            delivery=_delivery("delivery"),
        )
    with pytest.raises(ValidationError, match="delivery context belongs to a different project"):
        SoftwareContractContext(
            project_id="project-a",
            base_sha="a" * 40,
            repository_outcome=SoftwareRepositoryOutcome.MERGED,
            repository_criterion_id="repository",
            delivery=_delivery("delivery", project_id="project-b"),
        )


def test_global_and_literal_global_project_scopes_remain_distinct() -> None:
    context = SoftwareContractContext(
        project_id=None,
        base_sha="a" * 40,
        repository_outcome=SoftwareRepositoryOutcome.VERIFIED_COMMIT,
        repository_criterion_id="repository",
    )
    with pytest.raises(ValueError, match="software context belongs to a different project"):
        context.validate_contract(_contract("repository", project_id="global"))
