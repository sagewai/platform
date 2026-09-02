# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Base-moved GitHub lifecycle tests."""

from __future__ import annotations

import pytest

from sagewai.work import (
    GateDecision,
    PendingAttentionKind,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.profiles.software import SoftwareRepositoryOutcome
from sagewai.work.profiles.software.github import (
    BaseMovedError,
    GitHubIssueLifecycle,
    require_merge_approval,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.test_github import (
    ISSUE_URL,
    NOW,
    PROJECT_ID,
    FakeBranchPublisher,
    FakeGitHub,
    FakeSoftwareLifecycle,
    _flow,
)


@pytest.fixture
async def store(dialect_engine) -> WorkStore:  # noqa: F811
    result = WorkStore(engine=dialect_engine)
    await result.init()
    return result


@pytest.mark.asyncio
async def test_base_moved_before_publication_holds_the_work(
    store: WorkStore,
) -> None:
    lifecycle, _software, github, publisher = _flow(store)
    publisher.fail_phases = {"publish"}

    record = await lifecycle.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="base",
    )

    assert record.status == "BASE_MOVED"
    assert publisher.calls == []
    assert github.pull_requests == []
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    moved = [event for event in events if event.event_type is WorkEventType.BASE_MOVED]
    assert moved[-1].payload_json == {
        "phase": "publish",
        "expected_base": "base",
        "found_base": "other",
    }
    assert not any(event.event_type is WorkEventType.CONTROL_DEGRADED for event in events)
    pending = await store.pending_attention(project_id=PROJECT_ID)
    assert pending[0].kind is PendingAttentionKind.WORK_BLOCKED
    assert "publish" in pending[0].summary
    assert github.comments
    assert "default branch moved" in github.comments[-1][1]
    assert "supersede and rerun" in github.comments[-1][1]


@pytest.mark.asyncio
async def test_base_moved_during_intake_raises_without_creating_work(
    store: WorkStore,
) -> None:
    lifecycle, software, _github, publisher = _flow(store)
    publisher.fail_phases = {"intake"}

    with pytest.raises(BaseMovedError):
        await lifecycle.start(
            issue_url=ISSUE_URL,
            project_id=PROJECT_ID,
            base_sha="base",
        )

    assert software.starts == []
    assert await store.list_work(project_id=PROJECT_ID) == []


@pytest.mark.asyncio
async def test_base_moved_before_merge_holds_the_work_without_merging(
    store: WorkStore,
) -> None:
    lifecycle, _software, github, publisher = _flow(
        store,
        decision=GateDecision.ALLOW,
    )
    publisher.fail_phases = {"merge"}

    record = await lifecycle.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="base",
    )

    assert record.status == "BASE_MOVED"
    assert github.merges == []
    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    moved = [event for event in events if event.event_type is WorkEventType.BASE_MOVED]
    assert moved[-1].payload_json == {
        "phase": "merge",
        "expected_base": "base",
        "found_base": "other",
    }


@pytest.mark.asyncio
async def test_resume_after_base_moved_revalidates(
    store: WorkStore,
) -> None:
    lifecycle, software, github, publisher = _flow(store)
    publisher.fail_phases = {"publish"}
    held = await lifecycle.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="base",
    )
    publisher.fail_phases = set()

    record = await lifecycle.resume(held.work_id, project_id=PROJECT_ID)

    assert record.status == "READY_TO_MERGE"
    assert record.pending_gate == f"merge:{held.work_id}:7"
    assert len(publisher.calls) == 1
    assert len(github.pull_requests) == 1
    assert software.resumes == 0


@pytest.mark.asyncio
async def test_merge_base_moved_resume_keeps_gate_decision(
    store: WorkStore,
) -> None:
    software = FakeSoftwareLifecycle(store)
    github = FakeGitHub()
    publisher = FakeBranchPublisher()
    lifecycle = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=software,
        github=github,
        branch_publisher=publisher,
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        merge_policy=require_merge_approval,
    )

    gated = await lifecycle.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="base",
    )

    assert gated.status == "READY_TO_MERGE"
    assert gated.pending_gate == f"merge:{gated.work_id}:7"

    publisher.fail_phases = {"merge"}
    held = await lifecycle.approve(
        gated.work_id,
        project_id=PROJECT_ID,
        gate_id=gated.pending_gate,
        actor_ref="operator:arda",
    )

    assert held.status == "BASE_MOVED"
    assert held.pending_gate is None
    assert github.merges == []
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.GATE_REQUESTED for event in events) == 1
    assert sum(event.event_type is WorkEventType.GATE_DECIDED for event in events) == 1
    moved = [event for event in events if event.event_type is WorkEventType.BASE_MOVED]
    assert len(moved) == 1
    assert moved[0].payload_json == {
        "phase": "merge",
        "expected_base": "base",
        "found_base": "other",
    }

    still_held = await lifecycle.resume(gated.work_id, project_id=PROJECT_ID)

    assert still_held.status == "BASE_MOVED"
    assert github.merges == []
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.BASE_MOVED for event in events) == 1

    publisher.fail_phases = set()
    final = await lifecycle.resume(gated.work_id, project_id=PROJECT_ID)

    assert final.status == "COMPLETE"
    assert final.pending_gate is None
    assert len(github.merges) == 1
    events = await store.read_events(gated.work_id, project_id=PROJECT_ID)
    assert sum(event.event_type is WorkEventType.GATE_REQUESTED for event in events) == 1
    assert sum(event.event_type is WorkEventType.GATE_DECIDED for event in events) == 1


@pytest.mark.asyncio
async def test_github_resume_superseded_returns_record_without_side_effects(
    store: WorkStore,
) -> None:
    lifecycle, software, _github, _publisher = _flow(store)
    record = WorkRecord(
        work_id="work-1",
        project_id=PROJECT_ID,
        source_ref=ISSUE_URL,
        profile="software",
        status="SUPERSEDED",
        contract_version=1,
        active_run_id=None,
        pending_gate=None,
        profile_context={},
        created_at=NOW,
        updated_at=NOW,
    )
    await store.save_work(record)
    before = await store.read_events("work-1", project_id=PROJECT_ID)

    resumed = await lifecycle.resume("work-1", project_id=PROJECT_ID)
    after = await store.read_events("work-1", project_id=PROJECT_ID)

    assert resumed == record
    assert after == before == []
    assert software.resumes == 0


@pytest.mark.asyncio
async def test_merge_action_is_compensatable_with_rollback_and_post_check(
    store: WorkStore,
) -> None:
    lifecycle = GitHubIssueLifecycle(
        work_store=store,
        software_lifecycle=FakeSoftwareLifecycle(store),
        github=FakeGitHub(),
        branch_publisher=FakeBranchPublisher(),
        repository_outcome=SoftwareRepositoryOutcome.MERGED,
        merge_policy=require_merge_approval,
    )

    record = await lifecycle.start(
        issue_url=ISSUE_URL,
        project_id=PROJECT_ID,
        base_sha="base",
    )

    events = await store.read_events(record.work_id, project_id=PROJECT_ID)
    requested = next(event for event in events if event.event_type is WorkEventType.GATE_REQUESTED)
    action = requested.payload_json["action"]
    assert action["reversibility"] == "compensatable"
    assert action["rollback"] == "revert_pull_request"
    assert action["post_check"] == "merged_sha_read_back"
