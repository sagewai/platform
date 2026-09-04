# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Active report implementation of the generic Work profile seam."""

from __future__ import annotations

from sagewai.work.completion import validate_verification_result
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
from sagewai.work.profiles.report.models import ReportContractContext


class ReportProfile:
    """Plan and validate the one report action (spec section 12)."""

    name = "report"

    async def prepare(self, work: WorkItem, contract: WorkContract) -> ActionPlan:
        if work.project_id is None:
            raise ValueError("report profile requires a project")
        if work.profile != self.name:
            raise ValueError("work belongs to a different profile")
        if contract.project_id != work.project_id or contract.work_id != work.id:
            raise ValueError("contract belongs to different work")
        context = ReportContractContext.model_validate(contract.profile_context)
        context.validate_contract(contract)
        return ActionPlan(
            project_id=work.project_id,
            work_id=work.id,
            profile=self.name,
            actions=(
                Action(
                    id=f"{work.id}:compose",
                    project_id=work.project_id,
                    work_id=work.id,
                    profile=self.name,
                    target_system="report",
                    capability="browser.read",
                    scope={"allowed_hosts": list(context.allowed_hosts)},
                    inputs={},
                    expected_effect=contract.goal,
                    reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
                    preconditions=(),
                    verification=tuple(
                        criterion.id
                        for criterion in contract.acceptance_criteria
                        if criterion.verification_kind == "profile"
                    ),
                ),
            ),
        )

    async def verify(
        self,
        work: WorkItem,
        contract: WorkContract,
        criterion_ids: tuple[str, ...],
        actions: tuple[ActionResult, ...],
    ) -> VerificationResult:
        """The deterministic checks already ran; this records their verdict per criterion."""
        context = ReportContractContext.model_validate(contract.profile_context)
        context.validate_contract(contract)
        if not actions:
            raise ValueError("report verification requires action results")
        passed = all(result.status == "succeeded" for result in actions)
        evidence_refs = tuple(
            dict.fromkeys(ref for result in actions for ref in result.evidence_refs)
        )
        verification = VerificationResult(
            project_id=work.project_id,
            contract_id=contract.id,
            attempt_id=actions[0].action_id,
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
