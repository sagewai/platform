# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Base-moved step Works are confirmed, superseded, and rerun through the coordinator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import SUPERSEDED, WorkRecord
from sagewai.work.store import WorkStore
from sagewai.work.supersede import supersede_work
from sagewai.work.tasks.assessment import AssessmentGap
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import Authority, GateMode, TaskDefaults, TaskStatus
from sagewai.work.tasks.planner import PlanningFailedError
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_coordinator import _drive_to_rest, _fixed_task, _plan_result, _seed
from tests.work.tasks.test_decide import NOW

PROJECT = "project-a"


@pytest.fixture
async def stores(dialect_engine):  # noqa: F811
    from tests.work.tasks.test_store import _task

    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id=PROJECT, target=_task().target), expected_revision=0
    )
    return task_store, work_store


async def _record_active_step(task_store: TaskStore, record, *, issue_url: str, work_id: str):
    plan = _plan_result()
    return await TaskWriter(task_store).append(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": 1,
                    "steps": [step.model_dump(mode="json") for step in plan.steps],
                    "acceptance_matrix": [
                        item.model_dump(mode="json") for item in plan.acceptance_matrix
                    ],
                },
            ),
            (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
            status_entry(record, TaskStatus.EXECUTING),
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
            (
                TaskEventType.STEP_WORK_STARTED,
                {
                    "step_id": "s1",
                    "work_id": work_id,
                    "issue_url": issue_url,
                    "base_sha": "a" * 40,
                },
            ),
        ],
        now=NOW,
    )


async def _save_work(
    work_store: WorkStore,
    *,
    task_id: str,
    work_id: str,
    issue_url: str,
    status: str,
    base_sha: str,
) -> None:
    now = datetime.now(timezone.utc)
    github = {"merged_sha": "c" * 40} if status == "COMPLETE" else {}
    await work_store.save_work(
        WorkRecord(
            work_id=work_id,
            project_id=PROJECT,
            source_ref=issue_url,
            profile="software",
            status=status,
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context={"task_id": task_id, "github": github, "base_sha": base_sha},
            created_at=now,
            updated_at=now,
        )
    )


async def _base_moved_work(
    work_store: WorkStore,
    *,
    task_id: str,
    work_id: str,
    issue_url: str,
    phase: str,
) -> None:
    await _save_work(
        work_store,
        task_id=task_id,
        work_id=work_id,
        issue_url=issue_url,
        status="BASE_MOVED",
        base_sha="a" * 40,
    )
    await work_store.append_event(
        WorkEvent(
            id=f"{work_id}:moved:{phase}",
            project_id=PROJECT,
            work_id=work_id,
            sequence=1,
            event_type=WorkEventType.BASE_MOVED,
            actor_type="system",
            actor_ref="github",
            payload_json={"phase": phase, "expected_base": "a" * 40, "found_base": "b" * 40},
            created_at=NOW,
        )
    )


@pytest.mark.asyncio
async def test_a_publish_phase_hold_supersedes_and_reruns_with_the_prior_work_as_evidence(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "BASE_MOVED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    old_work = runner.started[0]
    assert (await work_store.load_work(old_work, project_id=PROJECT)).status == SUPERSEDED
    events = await task_store.read_events(task.id, project_id=PROJECT)
    replaced = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_SUPERSEDED)
    replacement = replaced.payload_json["superseded_by"]
    assert runner.evidence[runner.started.index(replacement)] == (f"work://{old_work}",)
    started = [e for e in events if e.event_type is TaskEventType.STEP_WORK_STARTED]
    restarted = next(e for e in started if e.payload_json["work_id"] == replacement)
    assert restarted.payload_json["base_sha"] == "d" * 40
    assert restarted.payload_json["issue_url"] == started[0].payload_json["issue_url"]
    assert TaskEventType.TASK_MESSAGE not in [e.event_type for e in events]
    assert record.status is not TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_the_replacement_contract_cites_the_superseded_pull_request(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "BASE_MOVED"
    runner.pull_request_urls["w-s1-1"] = "https://github.com/o/r/pull/7"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    replaced = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_SUPERSEDED)
    replacement = replaced.payload_json["superseded_by"]
    assert runner.evidence[runner.started.index(replacement)] == (
        "https://github.com/o/r/pull/7",
    )


@pytest.mark.asyncio
async def test_a_merge_phase_hold_on_an_already_merged_pull_request_resumes_instead(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    issue_url = "https://github.com/o/r/issues/1"
    record = await _record_active_step(task_store, record, issue_url=issue_url, work_id="w-s1-1")
    await _base_moved_work(
        work_store, task_id=task.id, work_id="w-s1-1", issue_url=issue_url, phase="merge"
    )
    runner.started.append("w-s1-1")
    runner.merged = True
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert runner.resumed == ["w-s1-1"]
    assert (await work_store.load_work("w-s1-1", project_id=PROJECT)).status != SUPERSEDED
    types = [e.event_type for e in await task_store.read_events(task.id, project_id=PROJECT)]
    assert TaskEventType.STEP_WORK_SUPERSEDED not in types
    assert record.status is TaskStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_merge_phase_hold_on_an_unmerged_pull_request_supersedes(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    issue_url = "https://github.com/o/r/issues/1"
    record = await _record_active_step(task_store, record, issue_url=issue_url, work_id="w-s1-1")
    await _base_moved_work(
        work_store, task_id=task.id, work_id="w-s1-1", issue_url=issue_url, phase="merge"
    )
    runner.started.append("w-s1-1")
    runner.head = "d" * 40
    runner.merged = False
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    types = [e.event_type for e in await task_store.read_events(task.id, project_id=PROJECT)]
    assert TaskEventType.STEP_WORK_SUPERSEDED in types
    assert record.status is TaskStatus.COMPLETE


@pytest.mark.asyncio
async def test_a_base_moved_error_out_of_start_surfaces_to_the_runner(
    stores, tmp_path, monkeypatch
) -> None:
    from sagewai.work.profiles.software.github import BaseMovedError

    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    original = runner.start
    calls = {"n": 0}

    async def flaky(task_, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            runner.head = "d" * 40
            raise BaseMovedError(expected=kwargs["base_sha"], found="d" * 40)
        return await original(task_, **kwargs)

    runner.start = flaky
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    with pytest.raises(BaseMovedError):
        await coordinator.drive(record, lease_epoch=epoch)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_an_already_superseded_base_move_uses_the_existing_replacement_work(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    issue_url = "https://github.com/o/r/issues/1"
    record = await _record_active_step(task_store, record, issue_url=issue_url, work_id="w-s1-1")
    await _base_moved_work(
        work_store, task_id=task.id, work_id="w-s1-1", issue_url=issue_url, phase="publish"
    )
    await _save_work(
        work_store,
        task_id=task.id,
        work_id="w-s1-existing",
        issue_url=issue_url,
        status="COMPLETE",
        base_sha="d" * 40,
    )
    await supersede_work(
        work_store,
        work_id="w-s1-1",
        project_id=PROJECT,
        superseded_by="w-s1-existing",
        reason="base_moved",
        actor_ref="external",
    )
    runner.started.extend(["w-s1-1", "w-s1-existing"])
    runner.head = "d" * 40
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert [work_id for work_id in runner.started if work_id.startswith("w-s1-")] == [
        "w-s1-1",
        "w-s1-existing",
    ]
    events = await task_store.read_events(task.id, project_id=PROJECT)
    superseded = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_SUPERSEDED)
    assert superseded.payload_json["superseded_by"] == "w-s1-existing"
    assert record.status is TaskStatus.COMPLETE


@pytest.mark.asyncio
async def test_planning_failure_after_replan_does_not_reuse_previous_assessment_gaps(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.AUTO)})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.assessor_verdict = "replan"
    runner.assessor_gaps = (
        AssessmentGap(
            statement="stale assessment gap",
            severity="high",
            suggested_step="repair-step",
        ),
    )
    original_plan = runner.plan
    calls = {"n": 0}

    async def fail_second_plan(task_, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PlanningFailedError("planner unavailable")
        return await original_plan(task_, **kwargs)

    runner.plan = fail_second_plan
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    message = [e for e in events if e.event_type is TaskEventType.TASK_MESSAGE][-1]
    assert record.status is TaskStatus.BLOCKED
    assert "planning failed: planner unavailable" in message.payload_json["text"]
    assert "stale assessment gap" not in message.payload_json["text"]
    assert "repair-step" not in message.payload_json["text"]
