# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the report Work profile action and verification contract."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work import (
    AcceptanceCriterion,
    ActionResult,
    Reversibility,
    WorkContract,
    WorkItem,
    WorkProfile,
)
from sagewai.work.profiles.report import ReportContractContext, ReportProfile
from sagewai.work.tasks.models import Sink

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _work_item(*, project_id: str = "project-a", profile: str = "report") -> WorkItem:
    return WorkItem(
        id="work-1",
        project_id=project_id,
        profile=profile,
        source="task",
        source_ref="task://task-1",
        title="Research report",
        description="Compose a sourced report",
        target_systems=("report",),
        created_at=NOW,
    )


def _contract(
    *,
    project_id: str = "project-a",
    work_id: str = "work-1",
    context_project_id: str | None = "project-a",
    report_criterion_id: str = "criterion-grounded",
) -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id=project_id,
        work_id=work_id,
        version=1,
        goal="Compose a sourced report",
        allowed_scope=(),
        acceptance_criteria=(
            AcceptanceCriterion(
                id="criterion-grounded",
                project_id=project_id,
                statement="Every claim cites a source snapshot",
                verification_kind="profile",
            ),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=("task://task-1",),
        assumption_ids=(),
        risk="low",
        design_required=False,
        profile_context=ReportContractContext(
            project_id=context_project_id,
            task_id="task-1",
            cycle=1,
            report_criterion_id=report_criterion_id,
            required_sections=("Summary",),
            allowed_hosts=("a.example",),
            sinks=(Sink(kind="console", version=1),),
        ).model_dump(mode="json"),
    )


@pytest.mark.asyncio
async def test_report_profile_rejects_a_software_work() -> None:
    with pytest.raises(ValueError, match="different profile"):
        await ReportProfile().prepare(_work_item(profile="software"), _contract())


@pytest.mark.asyncio
async def test_report_profile_rejects_a_foreign_contract() -> None:
    with pytest.raises(ValueError, match="different work"):
        await ReportProfile().prepare(_work_item(), _contract(project_id="project-b"))


@pytest.mark.asyncio
async def test_report_profile_rejects_a_context_from_another_project() -> None:
    with pytest.raises(ValueError, match="different project"):
        await ReportProfile().prepare(
            _work_item(),
            _contract(context_project_id="project-b"),
        )


@pytest.mark.asyncio
async def test_report_profile_rejects_a_context_with_an_unknown_report_criterion() -> None:
    with pytest.raises(ValueError, match="not in the accepted contract"):
        await ReportProfile().prepare(
            _work_item(),
            _contract(report_criterion_id="missing"),
        )


def test_report_contract_context_validates_sinks_at_model_time() -> None:
    with pytest.raises(ValidationError):
        ReportContractContext(
            project_id="project-a",
            task_id="task-1",
            cycle=1,
            report_criterion_id="criterion-grounded",
            sinks=({"kind": "github_issue", "version": 1},),
        )


@pytest.mark.asyncio
async def test_report_profile_prepares_one_compose_action() -> None:
    profile = ReportProfile()

    assert isinstance(profile, WorkProfile)
    plan = await profile.prepare(_work_item(), _contract())

    assert plan.project_id == "project-a"
    assert plan.work_id == "work-1"
    assert plan.profile == "report"
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.id == "work-1:compose"
    assert action.target_system == "report"
    assert action.capability == "browser.read"
    assert action.scope == {"allowed_hosts": ["a.example"]}
    assert action.inputs == {}
    assert action.expected_effect == "Compose a sourced report"
    assert action.reversibility is Reversibility.SNAPSHOT_REVERSIBLE
    assert action.preconditions == ()
    assert action.verification == ("criterion-grounded",)


@pytest.mark.asyncio
async def test_report_profile_verification_fails_when_any_receipt_failed() -> None:
    result = await ReportProfile().verify(
        _work_item(),
        _contract(),
        ("criterion-grounded",),
        (
            ActionResult(
                project_id="project-a",
                action_id="work-1:compose",
                status="failed",
                external_ref=None,
                evidence_refs=("artifact://sha256:" + "a" * 64,),
                started_at=NOW,
                completed_at=NOW,
            ),
        ),
    )

    assert result.attempt_id == "work-1:compose"
    assert result.passed is False
    assert result.evidence_refs == ("artifact://sha256:" + "a" * 64,)
    assert result.criterion_results[0].criterion_id == "criterion-grounded"
    assert result.criterion_results[0].passed is False


@pytest.mark.asyncio
async def test_report_profile_verification_passes_and_dedupes_receipt_evidence() -> None:
    result = await ReportProfile().verify(
        _work_item(),
        _contract(),
        ("criterion-grounded",),
        (
            ActionResult(
                project_id="project-a",
                action_id="work-1:compose",
                status="succeeded",
                external_ref=None,
                evidence_refs=("artifact://sha256:" + "a" * 64,),
                started_at=NOW,
                completed_at=NOW,
            ),
            ActionResult(
                project_id="project-a",
                action_id="work-1:compose",
                status="succeeded",
                external_ref=None,
                evidence_refs=("artifact://sha256:" + "a" * 64,),
                started_at=NOW,
                completed_at=NOW,
            ),
        ),
    )

    assert result.passed is True
    assert result.evidence_refs == ("artifact://sha256:" + "a" * 64,)
    assert result.criterion_results[0].passed is True
