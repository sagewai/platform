# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Active software implementation of the generic Work profile seam."""

from __future__ import annotations

from sagewai.work.completion import (
    validate_criterion_subset,
    validate_verification_result,
)
from sagewai.work.contract import WorkContract
from sagewai.work.models import (
    Action,
    ActionPlan,
    ActionResult,
    CriterionVerification,
    Reversibility,
    VerificationResult,
    WorkItem,
)
from sagewai.work.profiles.software.models import SoftwareContractContext
from sagewai.work.profiles.software.scm import SOFTWARE_WORKSPACE_PRECONDITION_ID


class SoftwareProfile:
    """Plan and validate the one currently implemented software action."""

    name = "software"

    async def prepare(
        self,
        work: WorkItem,
        contract: WorkContract,
    ) -> ActionPlan:
        if work.project_id is None:
            raise ValueError("software profile requires a project")
        if work.profile != self.name:
            raise ValueError("work belongs to a different profile")
        if contract.project_id != work.project_id or contract.work_id != work.id:
            raise ValueError("contract belongs to different work")
        if not contract.allowed_scope:
            raise ValueError("software contract requires an allowed scope")
        context = SoftwareContractContext.model_validate(contract.profile_context)
        context.validate_contract(contract)
        delivery_criterion_ids = (
            set(context.delivery.criterion_ids) if context.delivery is not None else set()
        )
        execution_criterion_ids = tuple(
            criterion.id
            for criterion in contract.acceptance_criteria
            if criterion.id != context.repository_criterion_id
            and criterion.id not in delivery_criterion_ids
        )
        action = Action(
            id=f"{work.id}:change",
            project_id=work.project_id,
            work_id=work.id,
            profile=self.name,
            target_system="repository",
            capability="filesystem.write",
            scope={"allowed_targets": list(contract.allowed_scope)},
            inputs={},
            expected_effect=contract.goal,
            reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
            preconditions=(SOFTWARE_WORKSPACE_PRECONDITION_ID,),
            verification=execution_criterion_ids,
        )
        return ActionPlan(
            project_id=work.project_id,
            work_id=work.id,
            profile=self.name,
            actions=(action,),
        )

    async def verify(
        self,
        work: WorkItem,
        contract: WorkContract,
        criterion_ids: tuple[str, ...],
        actions: tuple[ActionResult, ...],
    ) -> VerificationResult:
        if work.project_id is None:
            raise ValueError("software profile verification requires a project")
        if work.profile != self.name:
            raise ValueError("work belongs to a different profile")
        if contract.project_id != work.project_id or contract.work_id != work.id:
            raise ValueError("contract belongs to different work")
        context = SoftwareContractContext.model_validate(contract.profile_context)
        context.validate_contract(contract)
        delivery_criterion_ids = (
            set(context.delivery.criterion_ids) if context.delivery is not None else set()
        )
        execution_criterion_ids = tuple(
            criterion.id
            for criterion in contract.acceptance_criteria
            if criterion.id != context.repository_criterion_id
            and criterion.id not in delivery_criterion_ids
        )
        validate_criterion_subset(contract, criterion_ids)
        if criterion_ids != execution_criterion_ids:
            raise ValueError("criterion subset does not match the software execution action")
        if not actions:
            raise ValueError("software profile verification requires action results")

        attempts: set[str] = set()
        for result in actions:
            if result.project_id != work.project_id:
                raise ValueError("action result belongs to a different project")
            suffix = ":change"
            if not result.action_id.startswith(f"{work.id}:") or not result.action_id.endswith(
                suffix
            ):
                raise ValueError("action result belongs to a different work")
            attempts.add(result.action_id[: -len(suffix)])
        if len(attempts) != 1:
            raise ValueError("action results belong to different attempts")

        passed = all(result.status == "succeeded" for result in actions)
        evidence_refs = tuple(
            dict.fromkeys(ref for result in actions for ref in result.evidence_refs)
        )
        verification = VerificationResult(
            project_id=work.project_id,
            contract_id=contract.id,
            attempt_id=attempts.pop(),
            stage="execution",
            passed=passed,
            criterion_results=tuple(
                CriterionVerification(
                    project_id=work.project_id,
                    contract_id=contract.id,
                    criterion_id=criterion_id,
                    passed=passed,
                    evidence_refs=evidence_refs,
                )
                for criterion_id in criterion_ids
            ),
            evidence_refs=evidence_refs,
        )
        validate_verification_result(contract, criterion_ids, verification)
        return verification
