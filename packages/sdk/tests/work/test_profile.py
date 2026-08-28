# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the generic Work profile seam and active software profile."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sagewai.work import (
    Action,
    ActionPlan,
    ActionResult,
    Reversibility,
    WorkContract,
    WorkItem,
    WorkProfile,
)
from sagewai.work.profiles.software import SoftwareContractContext, SoftwareProfile

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _work_item(*, project_id: str = "project-a") -> WorkItem:
    return WorkItem(
        id="work-1",
        project_id=project_id,
        profile="software",
        source="local",
        source_ref=None,
        title="Change target",
        description="Change target deterministically",
        created_at=NOW,
    )


def _contract(*, project_id: str = "project-a") -> WorkContract:
    return WorkContract(
        id="contract-1",
        project_id=project_id,
        work_id="work-1",
        version=1,
        goal="Change target deterministically",
        allowed_scope=("target.txt",),
        acceptance_criteria=("deterministic verification passes",),
        constraints=(),
        non_goals=(),
        evidence_refs=("issue://1",),
        assumption_ids=(),
        risk="low",
        design_required=False,
        profile_context=SoftwareContractContext(base_sha="a" * 40).model_dump(
            mode="json"
        ),
    )


@pytest.mark.asyncio
async def test_software_profile_prepares_and_verifies_one_scoped_action() -> None:
    profile = SoftwareProfile()
    work_item = _work_item()

    assert isinstance(profile, WorkProfile)
    plan = await profile.prepare(work_item, _contract())
    assert plan.project_id == "project-a"
    assert plan.work_id == work_item.id
    assert plan.profile == "software"
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.project_id == "project-a"
    assert action.work_id == work_item.id
    assert action.capability == "filesystem.write"
    assert action.scope == {"allowed_targets": ["target.txt"]}
    assert action.verification == ("deterministic verification passes",)

    result = await profile.verify(
        work_item,
        (
            ActionResult(
                project_id="project-a",
                action_id="work-1:implement:1:change",
                status="succeeded",
                external_ref=None,
                evidence_refs=("runtime://work-1:implement:1",),
                started_at=NOW,
                completed_at=NOW,
            ),
        ),
    )

    assert result.attempt_id == "work-1:implement:1"
    assert result.passed is True
    assert result.evidence_refs == ("runtime://work-1:implement:1",)


def test_action_plan_rejects_cross_project_actions() -> None:
    action = Action(
        id="work-1:change",
        project_id="project-a",
        work_id="work-1",
        profile="software",
        target_system="git-worktree",
        capability="filesystem.write",
        scope={"allowed_targets": ["target.txt"]},
        inputs={},
        expected_effect="Change target deterministically",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
    )

    with pytest.raises(ValidationError, match="action belongs to a different project"):
        ActionPlan(
            project_id="project-b",
            work_id="work-1",
            profile="software",
            actions=(action,),
        )
