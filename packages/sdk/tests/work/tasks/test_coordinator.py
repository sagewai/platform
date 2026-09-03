# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The coordinator drives a leased Task through plan, steps, assessment, and completion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import SUPERSEDED, ActionRequest, GateDecision, Reversibility, WorkRecord
from sagewai.work.store import WorkStore
from sagewai.work.tasks.assessment import AssessmentGap, TaskAssessmentResult
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decisions import ConsoleDecisionChannel, merge_policy_for, resolve_gate
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import (
    Authority,
    Budget,
    GateMode,
    Schedule,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskStatus,
)
from sagewai.work.tasks.plan import ClarificationQuestion, MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.service import TaskService
from sagewai.work.tasks.store import SpendReservation, TaskStore
from sagewai.work.tasks.writer import TaskWriter
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_decide import MATRIX, NOW, STEPS
from tests.work.tasks.test_store import _task

PROJECT = "project-a"


class FakeProfileRunner:
    """Every ProfileRunner side effect, recorded and replayable."""

    def __init__(self, work_store: WorkStore, *, plan_result=None) -> None:
        self._work_store = work_store
        self.head = "a" * 40
        self.issues: dict[str, str] = {}
        self.started: list[str] = []
        self.resumed: list[str] = []
        self.evidence: list[tuple[str, ...]] = []
        self.merged = False
        self.plan_result = plan_result
        self.statuses: dict[str, str] = {}
        self.merged_shas: dict[str, str] = {}
        self.pull_request_urls: dict[str, str] = {}
        self.created_issues: list[tuple[str, str]] = []
        self.ledgers: list = []

    def use_ledger(self, ledger) -> None:
        self.ledgers.append(ledger)

    async def base_sha(self, task):
        return self.head

    async def plan(self, task, *, cycle, plan_version, base_sha, brief_text, amendments):
        assert base_sha == self.head
        return self.plan_result

    async def find_issue(self, task, *, cycle, step):
        return self.issues.get(step.id)

    async def create_issue(self, task, *, cycle, step):
        url = f"https://github.com/o/r/issues/{len(self.issues) + 1}"
        self.issues[step.id] = url
        self.created_issues.append((step.id, url))
        return url

    async def find_work(self, task, *, issue_url, exclude=None):
        for work_id in self.started:
            record = await self._work_store.load_work(work_id, project_id=task.project_id)
            if record is None or record.source_ref != issue_url or record.status == SUPERSEDED:
                continue
            if exclude is not None and work_id == exclude:
                continue
            return record
        return None

    async def start(self, task, *, cycle, step, issue_url, base_sha, evidence_refs=()):
        work_id = f"w-{step.id}-{len(self.started) + 1}"
        self.started.append(work_id)
        self.evidence.append(tuple(evidence_refs))
        return await self._save(
            task,
            work_id,
            issue_url,
            self.statuses.pop(step.id, "COMPLETE"),
            base_sha=base_sha,
        )

    async def resume(self, task, *, cycle, work_id):
        self.resumed.append(work_id)
        record = await self._work_store.load_work(work_id, project_id=task.project_id)
        if record.status == "WORK_BLOCKED":
            events = await self._work_store.read_events(work_id, project_id=task.project_id)
            if not any(event.event_type is WorkEventType.WORK_BLOCKED for event in events):
                return record
        return await self._save(
            task,
            work_id,
            record.source_ref,
            "COMPLETE",
            base_sha=record.profile_context.get("base_sha"),
        )

    async def is_merged(self, task, *, work_id):
        return self.merged

    async def _save(self, task, work_id, issue_url, status, *, base_sha=None):
        now = datetime.now(timezone.utc)
        github: dict[str, str] = {}
        if status == "COMPLETE":
            github["merged_sha"] = self.merged_shas.get(work_id, "c" * 40)
        if work_id in self.pull_request_urls:
            github["pull_request_url"] = self.pull_request_urls[work_id]
        profile_context = {"task_id": task.id, "github": github}
        if base_sha is not None:
            profile_context["base_sha"] = base_sha
        record = WorkRecord(
            work_id=work_id,
            project_id=task.project_id,
            source_ref=issue_url,
            profile="software",
            status=status,
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context=profile_context,
            created_at=now,
            updated_at=now,
        )
        await self._work_store.save_work(record)
        if status == "BASE_MOVED":
            new_head = "d" * 40
            await self._work_store.append_event(
                WorkEvent(
                    id=f"{work_id}:base-moved",
                    project_id=task.project_id,
                    work_id=work_id,
                    sequence=1,
                    event_type=WorkEventType.BASE_MOVED,
                    actor_type="system",
                    actor_ref="test",
                    payload_json={
                        "phase": "publish",
                        "expected_base": self.head,
                        "found_base": new_head,
                    },
                    created_at=now,
                )
            )
            self.head = new_head
        return record


def _plan_result(attempt_id: str = "plan") -> TaskPlanResult:
    return TaskPlanResult(
        attempt_id=attempt_id,
        steps=tuple(PlanStep.model_validate(step) for step in STEPS),
        acceptance_matrix=tuple(MatrixItem.model_validate(item) for item in MATRIX),
    )


class RecordingDecisionChannel:
    name = "recording"

    def __init__(self) -> None:
        self.calls = []

    async def notify(self, decision):
        self.calls.append(decision)
        return f"recording:{decision.task_id}:{decision.attention_id}:{len(self.calls)}"


@pytest.fixture
async def stores(dialect_engine):  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id=PROJECT, target=_task().target), expected_revision=0
    )
    return task_store, work_store


async def _seed(stores, tmp_path, *, plan_auto: bool = True):
    from sagewai.artifacts.object_store import LocalArtifactStore

    task_store, work_store = stores
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    service = TaskService(store=task_store, artifact_store=artifacts)
    task, record = await service.create(
        "Implement the retry queue in the payments service repository with a failing test first "
        "and open a pull request when the deterministic verification command passes.",
        project_id=PROJECT,
        origin=TaskOrigin.HUMAN,
        created_by="arda",
        now=NOW,
    )
    if plan_auto:
        task = task.model_copy(update={"authority": Authority(plan=GateMode.AUTO)})
    runner = FakeProfileRunner(work_store, plan_result=_plan_result())
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runner=runner,
        artifact_store=artifacts,
        decision_channels=(ConsoleDecisionChannel(),),
    )
    return task, record, runner, coordinator


async def _drive_to_rest(coordinator, record, epoch, *, limit=20):
    for _ in range(limit):
        before = record.revision
        record = await coordinator.drive(record, lease_epoch=epoch)
        if record.revision == before:
            return record
    return record


def _lose_the_batch(monkeypatch, *, kind: str):
    original = TaskWriter.append
    state = {"lost": 0}

    async def append(self, record, entries, **kwargs):
        head = entries[0]
        if (
            state["lost"] == 0
            and head[0] is TaskEventType.COMMAND_RECEIPT
            and head[1]["kind"] == kind
        ):
            state["lost"] += 1
            raise RuntimeError(f"crashed before the {kind} batch landed")
        return await original(self, record, entries, **kwargs)

    monkeypatch.setattr(TaskWriter, "append", append)
    return state


@pytest.mark.asyncio
async def test_plan_to_two_steps_to_assess_to_complete(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    for _ in range(20):
        record = await coordinator.drive(record, lease_epoch=epoch)
        if record.status in {TaskStatus.COMPLETE, TaskStatus.BLOCKED}:
            break
    assert record.status is TaskStatus.COMPLETE
    assert runner.started == ["w-s1-1", "w-s2-2"]
    events = await task_store.read_events(task.id, project_id=PROJECT)
    types = [event.event_type for event in events]
    assert types.count(TaskEventType.STEP_WORK_STARTED) == 2
    assert types.count(TaskEventType.STEP_WORK_OUTCOME) == 2
    assert TaskEventType.PLAN_ACCEPTED in types
    assert TaskEventType.CYCLE_STARTED in types
    assert TaskEventType.BASE_ADVANCED in types
    assert TaskEventType.ASSESSMENT_RECORDED in types
    assert TaskEventType.BUDGET_RECORDED in types
    assert types[-1] is TaskEventType.TASK_STATUS_CHANGED
    assert types.index(TaskEventType.CYCLE_STARTED) < types.index(TaskEventType.STEP_WORK_STARTED)
    assert types.index(TaskEventType.BUDGET_RECORDED) < types.index(TaskEventType.CYCLE_COMPLETED)
    assert len(runner.ledgers) == 3
    receipts = [event for event in events if event.event_type is TaskEventType.COMMAND_RECEIPT]
    assert [receipt.payload_json["kind"] for receipt in receipts] == [
        "run_planning",
        "start_cycle",
        "start_step",
        "record_step_outcome",
        "start_step",
        "record_step_outcome",
        "assess_cycle",
        "complete_cycle",
    ]
    assert set(receipts[0].payload_json) == {"command_id", "kind", "payload"}
    for receipt in receipts:
        assert events[events.index(receipt) + 1].event_type is not TaskEventType.COMMAND_RECEIPT


@pytest.mark.asyncio
async def test_a_completed_task_projects_telemetry_without_raising(stores, tmp_path, monkeypatch) -> None:
    from sagewai.work.tasks.telemetry import derive_task_telemetry

    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    for _ in range(20):
        record = await coordinator.drive(record, lease_epoch=epoch)
        if record.status is TaskStatus.COMPLETE:
            break
    events = await task_store.read_events(task.id, project_id=PROJECT)
    telemetry = derive_task_telemetry(
        record=record,
        task_events=events,
        work_events={
            work_id: await work_store.read_events(work_id, project_id=PROJECT)
            for work_id in runner.started
        },
        spend={
            1: await task_store.spend_totals(task_id=task.id, project_id=PROJECT, cycle=1),
        },
        budget=task.budget,
        project_selections=(),
        now=datetime.now(timezone.utc),
    )
    assert [cycle.cycle for cycle in telemetry.cycles] == [1]


@pytest.mark.asyncio
async def test_step_work_started_and_outcome_carry_the_telemetry_keys(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    for _ in range(8):
        record = await coordinator.drive(record, lease_epoch=epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    started = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_STARTED)
    outcome = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_OUTCOME)
    completed = next(e for e in events if e.event_type is TaskEventType.CYCLE_COMPLETED)
    budget = next(e for e in events if e.event_type is TaskEventType.BUDGET_RECORDED)
    assert set(started.payload_json) == {"step_id", "work_id", "issue_url", "base_sha"}
    assert set(outcome.payload_json) == {"step_id", "work_id", "outcome"}
    assert outcome.payload_json["outcome"] == "accepted"
    assert completed.payload_json["outcome"] == "succeeded"
    assert completed.payload_json["next_run_at"] is None
    assert set(budget.payload_json) == {"budget_used"}


@pytest.mark.asyncio
async def test_a_blocked_step_work_blocks_the_task_and_presents_the_decision(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "WORK_BLOCKED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)
    assert record.status is TaskStatus.EXECUTING
    assert runner.started == ["w-s1-1"]
    await work_store.append_event(
        WorkEvent(
            id="e1",
            project_id=PROJECT,
            work_id=runner.started[-1],
            sequence=1,
            event_type=WorkEventType.WORK_BLOCKED,
            actor_type="system",
            actor_ref="test",
            payload_json={
                "reason": "needs a decision",
                "decision_request": "choose the queue",
            },
            created_at=NOW,
        )
    )
    record = await coordinator.drive(record, lease_epoch=epoch)
    assert record.status is TaskStatus.BLOCKED
    assert record.attention_owner.value == "user"
    events = await task_store.read_events(task.id, project_id=PROJECT)
    types = [event.event_type for event in events]
    assert TaskEventType.TASK_MESSAGE in types
    assert TaskEventType.NOTIFICATION_PRESENTED in types
    presented = next(event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED)
    assert presented.payload_json["urgency"] == "now"
    assert set(presented.payload_json) == {"channel", "ref", "attention_id", "urgency", "due_at"}


@pytest.mark.asyncio
async def test_a_base_moved_work_is_superseded_and_rerun_on_the_new_head(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "BASE_MOVED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)
    superseded = await work_store.load_work(runner.started[0], project_id=PROJECT)
    assert superseded.status == SUPERSEDED
    assert runner.started[1] != runner.started[0]
    events = await task_store.read_events(task.id, project_id=PROJECT)
    replaced = next(e for e in events if e.event_type is TaskEventType.STEP_WORK_SUPERSEDED)
    assert replaced.payload_json["reason"] == "base_moved"
    assert replaced.payload_json["superseded_by"] == runner.started[1]
    restarted = [e for e in events if e.event_type is TaskEventType.STEP_WORK_STARTED]
    assert restarted[-1].payload_json["base_sha"] == "d" * 40


@pytest.mark.asyncio
async def test_a_stale_lease_epoch_never_reaches_a_side_effect(stores, tmp_path, monkeypatch) -> None:
    from sagewai.work.tasks.store import StaleTaskError

    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    before = await task_store.read_events(task.id, project_id=PROJECT)
    with pytest.raises(StaleTaskError):
        await coordinator.drive(record, lease_epoch=epoch - 1)
    assert runner.issues == {} and runner.started == []
    assert await task_store.read_events(task.id, project_id=PROJECT) == before


@pytest.mark.asyncio
async def test_a_crash_before_the_append_replays_onto_the_same_issue_and_work(
    stores, tmp_path, monkeypatch
) -> None:
    """Section 8.1: the receipt turns execute into a read-back, never a second Work."""
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    original = runner.start
    calls = {"n": 0}

    async def crash_after_start(task_, **kwargs):
        calls["n"] += 1
        await original(task_, **kwargs)
        raise RuntimeError("crashed between the side effect and the append")

    runner.start = crash_after_start
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)
    runner.start = original
    record = await coordinator.drive(record, lease_epoch=epoch)
    assert record.status is TaskStatus.COMPLETE
    assert calls["n"] == 1
    assert [step_id for step_id, _url in runner.created_issues].count("s1") == 1
    assert [work_id for work_id in runner.started if work_id.startswith("w-s1-")] == ["w-s1-1"]
    started = [
        event
        for event in await task_store.read_events(task.id, project_id=PROJECT)
        if event.event_type is TaskEventType.STEP_WORK_STARTED
        and event.payload_json["work_id"] == "w-s1-1"
    ]
    assert len(started) == 1
    assert started[0].payload_json["work_id"] == "w-s1-1"


@pytest.mark.asyncio
async def test_replay_after_create_issue_uses_the_existing_issue(stores, tmp_path, monkeypatch) -> None:
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    original = runner.create_issue

    async def crash_after_issue(task_, **kwargs):
        url = await original(task_, **kwargs)
        raise RuntimeError(f"crashed after creating {url}")

    runner.create_issue = crash_after_issue
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)
    runner.create_issue = original
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.status is TaskStatus.COMPLETE
    assert [step_id for step_id, _url in runner.created_issues].count("s1") == 1


@pytest.mark.asyncio
async def test_replay_after_supersede_rerun_start_uses_one_replacement_work(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "BASE_MOVED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    original = runner.start
    calls = {"n": 0}

    async def crash_on_replacement(task_, **kwargs):
        calls["n"] += 1
        work = await original(task_, **kwargs)
        if calls["n"] == 2:
            raise RuntimeError("crashed after the replacement Work was started")
        return work

    runner.start = crash_on_replacement
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)
    runner.start = original
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.status is TaskStatus.COMPLETE
    assert [work_id for work_id in runner.started if work_id.startswith("w-s1-")] == [
        "w-s1-1",
        "w-s1-2",
    ]
    old = await work_store.load_work("w-s1-1", project_id=PROJECT)
    assert old.status == SUPERSEDED


@pytest.mark.asyncio
async def test_replay_after_supersede_work_before_the_batch_completes_the_record(
    stores, tmp_path, monkeypatch
) -> None:
    import sagewai.work.tasks.coordinator as module

    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "BASE_MOVED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    original = module.supersede_work

    async def crash_after_supersede(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("crashed after supersede_work, before the batch")

    monkeypatch.setattr(module, "supersede_work", crash_after_supersede)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)
    monkeypatch.setattr(module, "supersede_work", original)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    superseded = [event for event in events if event.event_type is TaskEventType.STEP_WORK_SUPERSEDED]
    replacement_started = [
        event
        for event in events
        if event.event_type is TaskEventType.STEP_WORK_STARTED
        and event.payload_json["work_id"] == "w-s1-2"
    ]
    replacement_outcome = [
        event
        for event in events
        if event.event_type is TaskEventType.STEP_WORK_OUTCOME
        and event.payload_json["work_id"] == "w-s1-2"
    ]
    assert record.status is TaskStatus.COMPLETE
    assert len(superseded) == 1
    assert len(replacement_started) == 1
    assert len(replacement_outcome) == 1
    assert replacement_started[0].sequence < replacement_outcome[0].sequence


@pytest.mark.asyncio
async def test_work_gate_mirror_copies_the_action_and_notifies_today(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_channels", (channel,))

    async def no_progress(task_, *, cycle, work_id):
        runner.resumed.append(work_id)
        return await work_store.load_work(work_id, project_id=task_.project_id)

    runner.statuses["s1"] = "AWAITING_MERGE_APPROVAL"
    runner.resume = no_progress
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    work_id = runner.started[-1]
    stored = await work_store.load_work(work_id, project_id=PROJECT)
    await work_store.save_work(stored.model_copy(update={"pending_gate": "merge:w1:7"}))
    action = ActionRequest(
        project_id=PROJECT,
        action="merge",
        work_id=work_id,
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="https://github.com/o/r/pull/7",
        evidence_refs=("pr://7",),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    )
    await work_store.append_event(
        WorkEvent(
            id="gate-1",
            project_id=PROJECT,
            work_id=work_id,
            sequence=1,
            event_type=WorkEventType.GATE_REQUESTED,
            actor_type="system",
            actor_ref="test",
            payload_json={
                "gate_id": "merge:w1:7",
                "question": "Merge the pull request?",
                "action": action.model_dump(mode="json"),
                "evidence_refs": ("pr://7",),
            },
            created_at=NOW,
        )
    )
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    gate = next(event for event in events if event.event_type is TaskEventType.GATE_REQUESTED)
    presented = next(event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED)
    assert record.pending_gate == "merge:w1:7"
    assert record.attention_owner.value == "user"
    assert set(gate.payload_json) == {"gate_id", "question", "action", "work_id", "attention_id"}
    assert gate.payload_json["action"] == action.model_dump(mode="json")
    assert set(presented.payload_json) == {"channel", "ref", "attention_id", "urgency", "due_at"}
    assert presented.payload_json["urgency"] == "today"
    assert channel.calls[0].evidence_refs == ("pr://7", work_id)


@pytest.mark.asyncio
async def test_a_missing_work_gate_event_raises_the_named_value_error(
    stores, tmp_path, monkeypatch
) -> None:
    from sagewai.work.tasks.decide import MirrorAttention

    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    command = MirrorAttention(
        step_id="s1",
        work_id="work-without-gate",
        attention_kind="GATE_REQUESTED",
        attention_id="attention-1",
        summary="Approve the merge?",
        gate_id="merge:work-without-gate:1",
    )

    with pytest.raises(
        ValueError,
        match="Work work-without-gate has no GATE_REQUESTED for merge:work-without-gate:1",
    ):
        await coordinator._mirror(task, record, command, lease_epoch=1)


@pytest.mark.asyncio
async def test_a_lost_mirror_batch_notifies_the_channel_once(stores, tmp_path, monkeypatch) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_channels", (channel,))
    runner.statuses["s1"] = "WORK_BLOCKED"
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    await work_store.append_event(
        WorkEvent(
            id="blocked-1",
            project_id=PROJECT,
            work_id=runner.started[-1],
            sequence=1,
            event_type=WorkEventType.WORK_BLOCKED,
            actor_type="system",
            actor_ref="test",
            payload_json={"reason": "needs a decision", "decision_request": "choose the queue"},
            created_at=NOW,
        )
    )
    _lose_the_batch(monkeypatch, kind="mirror_attention")
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.status is TaskStatus.BLOCKED
    assert [call.attention_id for call in channel.calls] == ["blocked-1"]


@pytest.mark.asyncio
async def test_pruning_activity_includes_superseded_step_works(stores, tmp_path) -> None:
    from sagewai.work.tasks.decide import CycleState

    task_store, work_store = stores
    task, _record, runner, _coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(update={"retention_days": 7})
    calls = []

    class Activity:
        async def prune(self, **kwargs) -> None:
            calls.append(kwargs)

    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runner=runner,
        activity_store=Activity(),
    )
    state = CycleState(
        step_works={"s1": "work-new"},
        superseded_works=frozenset({"work-old"}),
    )

    await coordinator._prune_activity(task, state)

    assert set(calls[0]["completed_work_ids"]) == {"work-new", "work-old"}


@pytest.mark.asyncio
async def test_planning_gate_under_require_records_the_gate_payload(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path, plan_auto=False)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    gate = next(event for event in events if event.event_type is TaskEventType.GATE_REQUESTED)
    assert record.status is TaskStatus.PLAN_PROPOSED
    assert record.pending_gate == f"plan:{task.id}:1"
    assert set(gate.payload_json) == {"gate_id", "question", "action"}
    assert gate.payload_json["action"]["action"] == "plan"


@pytest.mark.asyncio
async def test_planning_clarification_uses_the_project_deadline(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.plan_result = TaskPlanResult(
        attempt_id="plan",
        clarifications=(
            ClarificationQuestion(
                id="q1",
                text="Which queue?",
                kind="choice",
                options=("redis", "sqs"),
                defaultable=False,
            ),
        ),
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    asked = [event for event in events if event.event_type is TaskEventType.CLARIFICATION_REQUESTED][-1]
    defaults = await task_store.get_defaults(project_id=PROJECT)
    assert record.status is TaskStatus.CLARIFYING
    assert asked.payload_json["deadline_at"] == (
        NOW + timedelta(seconds=defaults.clarification_deadline_seconds)
    ).isoformat()


@pytest.mark.asyncio
async def test_budget_exhaustion_records_ledger_usage_and_notifies_now(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_channels", (channel,))
    task = task.model_copy(update={"budget": Budget(max_cycle_usd=Decimal("0"))})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    await task_store.reserve_spend(
        SpendReservation(
            reservation_id="r-1",
            project_id=PROJECT,
            task_id=task.id,
            cycle=1,
            role="implementer",
            runtime="claude",
            usd_reserved=Decimal("1.00"),
        )
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    budget = [event for event in events if event.event_type is TaskEventType.BUDGET_RECORDED][-1]
    presented = [event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED][-1]
    assert record.status is TaskStatus.BUDGET_EXHAUSTED
    assert record.attention_owner.value == "user"
    assert budget.payload_json["budget_used"]["usd_reserved"] == "1.00"
    assert presented.payload_json["urgency"] == "now"
    assert channel.calls[-1].urgency == "now"


@pytest.mark.asyncio
async def test_ungated_replan_reenters_planning_and_runs_the_planner_again(
    stores, tmp_path, monkeypatch
) -> None:
    import sagewai.work.tasks.coordinator as module

    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.AUTO)})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    verdicts = iter(
        (
            TaskAssessmentResult(
                attempt_id="a1",
                gaps=(AssessmentGap(statement="gap", severity="high", suggested_step="s1"),),
                verdict="replan",
            ),
            TaskAssessmentResult(attempt_id="a2", verdict="accept"),
        )
    )
    monkeypatch.setattr(module, "assess_cycle", lambda *args, **kwargs: next(verdicts))
    plan_calls = {"n": 0}
    original_plan = runner.plan

    async def counted_plan(task_, **kwargs):
        plan_calls["n"] += 1
        return await original_plan(task_, **kwargs)

    runner.plan = counted_plan
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.status is TaskStatus.COMPLETE
    assert plan_calls["n"] == 2


@pytest.mark.asyncio
async def test_gated_replan_allow_returns_to_planning_and_runs_the_planner(
    stores, tmp_path, monkeypatch
) -> None:
    import sagewai.work.tasks.coordinator as module

    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.REQUIRE)})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    plan_calls = {"n": 0}
    original_plan = runner.plan

    async def counted_plan(task_, **kwargs):
        plan_calls["n"] += 1
        return await original_plan(task_, **kwargs)

    runner.plan = counted_plan
    monkeypatch.setattr(
        module,
        "assess_cycle",
        lambda *args, **kwargs: TaskAssessmentResult(
            attempt_id="a1",
            gaps=(AssessmentGap(statement="gap", severity="high", suggested_step="s1"),),
            verdict="replan",
        ),
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.pending_gate == f"replan:{task.id}:2"
    assert plan_calls["n"] == 1
    service = TaskService(store=task_store)
    decided = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=record.pending_gate,
        decision="allow",
        actor_ref="arda",
        now=NOW,
    )
    assert decided.status is TaskStatus.PLANNING
    await coordinator.drive(decided, lease_epoch=epoch)
    assert plan_calls["n"] == 2


@pytest.mark.asyncio
async def test_gated_replan_deny_blocks_for_the_user(stores, tmp_path, monkeypatch) -> None:
    import sagewai.work.tasks.coordinator as module

    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.REQUIRE)})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(
        module,
        "assess_cycle",
        lambda *args, **kwargs: TaskAssessmentResult(attempt_id="a1", verdict="replan"),
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    service = TaskService(store=task_store)
    blocked = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=record.pending_gate,
        decision="deny",
        actor_ref="arda",
        now=NOW,
    )
    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.attention_owner.value == "user"


@pytest.mark.asyncio
async def test_spent_replan_budget_blocks_with_gap_text_and_notifies_now(
    stores, tmp_path, monkeypatch
) -> None:
    import sagewai.work.tasks.coordinator as module

    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_channels", (channel,))
    task = task.model_copy(update={"budget": task.budget.model_copy(update={"max_replans": 0})})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(
        module,
        "assess_cycle",
        lambda *args, **kwargs: TaskAssessmentResult(
            attempt_id="a1",
            gaps=(
                AssessmentGap(
                    statement="deterministic check failed",
                    severity="high",
                    suggested_step="repair-step",
                ),
            ),
            verdict="replan",
        ),
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    message = [event for event in events if event.event_type is TaskEventType.TASK_MESSAGE][-1]
    presented = [event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED][-1]
    assert record.status is TaskStatus.BLOCKED
    assert "deterministic check failed" in message.payload_json["text"]
    assert "repair-step" in message.payload_json["text"]
    assert presented.payload_json["urgency"] == "now"
    assert channel.calls[-1].urgency == "now"


@pytest.mark.asyncio
async def test_repository_lease_held_by_another_task_starts_no_side_effect(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    record = await TaskWriter(task_store).append(
        record,
        [
            (
                TaskEventType.PLAN_PROPOSED,
                {
                    "version": 1,
                    "steps": [step.model_dump(mode="json") for step in _plan_result().steps],
                    "acceptance_matrix": [
                        item.model_dump(mode="json") for item in _plan_result().acceptance_matrix
                    ],
                },
            ),
            (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
        ],
        now=NOW,
    )
    assert await task_store.acquire_repository_lease(
        task.repository_lease_key,
        project_id=PROJECT,
        task_id="another-task",
        work_id=None,
        ttl_seconds=3600,
    )
    before_events = await task_store.read_events(task.id, project_id=PROJECT)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    after = await coordinator.drive(record, lease_epoch=epoch)
    assert after.status is record.status
    assert after.revision == record.revision
    assert runner.created_issues == []
    assert runner.started == []
    assert await task_store.read_events(task.id, project_id=PROJECT) == before_events


@pytest.mark.asyncio
async def test_scheduled_task_completes_the_cycle_back_to_scheduled(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "schedule": Schedule(cron="0 9 * * 1", timezone="Europe/Berlin"),
        }
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    completed = next(event for event in events if event.event_type is TaskEventType.CYCLE_COMPLETED)
    assert record.status is TaskStatus.SCHEDULED
    assert record.next_run_at is not None
    assert completed.payload_json["next_run_at"] is not None


@pytest.mark.asyncio
async def test_every_command_batch_is_headed_by_the_receipt(stores, tmp_path, monkeypatch) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    batches: list[list[TaskEventType]] = []
    original = TaskWriter.append

    async def capture(self, record_, entries, **kwargs):
        batches.append([entry[0] for entry in entries])
        return await original(self, record_, entries, **kwargs)

    monkeypatch.setattr(TaskWriter, "append", capture)
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.status is TaskStatus.COMPLETE
    assert all(batch[0] is TaskEventType.COMMAND_RECEIPT for batch in batches)
    assert all(batch.count(TaskEventType.COMMAND_RECEIPT) == 1 for batch in batches)


def test_resolve_gate_follows_reversibility() -> None:
    compensatable = ActionRequest(
        project_id=PROJECT,
        action="merge",
        work_id="w1",
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="pr",
        evidence_refs=(),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    )
    without_recipe = compensatable.model_copy(update={"rollback": None})
    irreversible = compensatable.model_copy(update={"reversibility": Reversibility.IRREVERSIBLE})
    assert resolve_gate(GateMode.AUTO, compensatable) is GateDecision.ALLOW
    assert resolve_gate(GateMode.REQUIRE, compensatable) is GateDecision.REQUIRE_APPROVAL
    assert resolve_gate(GateMode.BY_REVERSIBILITY, compensatable) is GateDecision.ALLOW
    assert resolve_gate(GateMode.BY_REVERSIBILITY, without_recipe) is GateDecision.REQUIRE_APPROVAL
    assert resolve_gate(GateMode.BY_REVERSIBILITY, irreversible) is GateDecision.REQUIRE_APPROVAL
    assert merge_policy_for(Authority(merge=GateMode.AUTO))(compensatable) is GateDecision.ALLOW


@pytest.mark.asyncio
async def test_trigger_origin_keeps_merge_approval_required(stores, tmp_path) -> None:
    from sagewai.artifacts.object_store import LocalArtifactStore

    task_store, _ = stores
    service = TaskService(
        store=task_store, artifact_store=LocalArtifactStore(root=tmp_path / "objects")
    )
    task, _record = await service.create(
        "Implement the retry queue in the payments service repository with a failing test first "
        "and open a pull request when the deterministic verification command passes.",
        project_id=PROJECT,
        origin=TaskOrigin.TRIGGER,
        created_by="trigger",
        authority_floor=Authority(merge=GateMode.BY_REVERSIBILITY),
        now=NOW,
    )
    merge = ActionRequest(
        project_id=PROJECT,
        action="merge",
        work_id="w1",
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="pr",
        evidence_refs=(),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    )
    assert merge_policy_for(task.authority)(merge) is GateDecision.REQUIRE_APPROVAL


def _fixed_task(store, task):
    """Return the amended Task definition without rewriting the stored row."""

    async def _load(task_id: str, project_id: str):
        record = await store.load_record(task_id, project_id=project_id)
        return task, record

    return _load
