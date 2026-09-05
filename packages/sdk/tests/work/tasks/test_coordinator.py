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
from pathlib import Path

import pytest

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.events import WorkEvent, WorkEventType
from sagewai.work.models import (
    SUPERSEDED,
    ActionRequest,
    ActionResult,
    GateDecision,
    Reversibility,
    WorkRecord,
)
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import DeliveryReceipt, RollbackExecutor, RollbackRefusedError
from sagewai.work.tasks.assessment import (
    AssessmentGap,
    MatrixResult,
    TaskAssessmentResult,
    merge_assessment,
)
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decisions import ConsoleDecisionChannel, merge_policy_for, resolve_gate
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import (
    AttentionOwner,
    Authority,
    Budget,
    GateMode,
    Schedule,
    SoftwareTarget,
    TaskDefaults,
    TaskKind,
    TaskOrigin,
    TaskStatus,
)
from sagewai.work.tasks.plan import ClarificationQuestion, MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.planner import PlanningFailedError
from sagewai.work.tasks.service import TaskService
from sagewai.work.tasks.store import SpendReservation, TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_actions import merged_repository  # noqa: F401
from tests.work.tasks.test_decide import MATRIX, NOW, STEPS
from tests.work.tasks.test_software_kernel import RecordingGitHub
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
        self.plan_error: Exception | None = None
        self.base_sha_error: Exception | None = None
        self.statuses: dict[str, str] = {}
        self.gates: dict[str, str] = {}
        self.delivered: list[tuple[str, int]] = []
        self.deliver_sink_versions: dict[str, int] = {}
        self.deliver_next_sink_versions: dict[tuple[str, int], int] = {}
        self.deliver_actions: dict[tuple[str, int], dict] = {}
        self.deliver_action: dict | None = None
        self.delivery_action_id: str | None = None
        self.delivery_external_ref: str | None = None
        self.delivery_passed = True
        self.clear_report_on_deliver = False
        self.merged_shas: dict[str, str] = {}
        self.pull_request_urls: dict[str, str] = {}
        self.created_issues: list[tuple[str, str]] = []
        self.ledgers: list = []
        self.assessed: list = []
        self.assessor_verdict = "accept"
        self.assessor_gaps = ()

    def use_ledger(self, ledger) -> None:
        self.ledgers.append(ledger)

    async def base_sha(self, task):
        if self.base_sha_error is not None:
            raise self.base_sha_error
        return self.head

    async def plan(self, task, *, cycle, plan_version, base_sha, brief_text, amendments):
        assert base_sha == self.head
        if self.plan_error is not None:
            raise self.plan_error
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
        record = await self._save(
            task,
            work_id,
            issue_url,
            self.statuses.pop(step.id, "COMPLETE"),
            base_sha=base_sha,
        )
        gate_id = self.gates.get(step.id)
        if gate_id is not None:
            record = await self._open_gate(task, record, gate_id)
        return record

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

    async def assess(self, task, *, cycle, plan_version, plan, outcomes, merged_sha, evidence):
        self.assessed.append((cycle, plan_version, merged_sha))
        attempt_id = f"{task.id}:assess:{cycle}:{plan_version}"
        return merge_assessment(
            plan,
            attempt_id=attempt_id,
            outcomes=outcomes,
            deterministic=tuple(
                MatrixResult(item_id=item.id, passed=True, evidence_refs=evidence)
                for item in plan.acceptance_matrix
                if item.verification_kind == "deterministic"
            ),
            assessor=TaskAssessmentResult(
                attempt_id=attempt_id,
                matrix_results=tuple(
                    MatrixResult(item_id=item.id, passed=True)
                    for item in plan.acceptance_matrix
                    if item.verification_kind != "deterministic"
                ),
                gaps=self.assessor_gaps,
                verdict=self.assessor_verdict,
            ),
        )

    async def deliver(self, task, *, work_id: str, sink_version: int):
        self.delivered.append((work_id, sink_version))
        record = await self._work_store.load_work(work_id, project_id=task.project_id)
        action = ActionRequest.model_validate(record.profile_context["report"]["deliver_action"])
        next_version = self.deliver_next_sink_versions.get((work_id, sink_version))
        if next_version is None:
            update = {"status": "COMPLETE"}
        else:
            self.deliver_sink_versions[work_id] = next_version
            profile_context = dict(record.profile_context)
            profile_context["report"] = {
                "pending_sink_version": next_version,
                "deliver_action": self.deliver_actions[(work_id, next_version)],
            }
            update = {"status": "READY_TO_DELIVER", "profile_context": profile_context}
        if self.clear_report_on_deliver:
            profile_context = dict(record.profile_context)
            profile_context.pop("report", None)
            update["profile_context"] = profile_context
        record = record.model_copy(update=update)
        await self._work_store.save_work(record)
        now = datetime.now(timezone.utc)
        action_id = self.delivery_action_id or f"deliver:{work_id}:{sink_version}"
        receipt = DeliveryReceipt(
            action=action,
            result=ActionResult(
                project_id=task.project_id,
                action_id=action_id,
                status="succeeded",
                external_ref=self.delivery_external_ref or action.scope,
                evidence_refs=action.evidence_refs,
                started_at=now,
                completed_at=now,
            ),
            observation={
                "action_id": action_id,
                "check": action.post_check,
                "passed": self.delivery_passed,
                "detail": f"delivered {self.delivery_external_ref or action.scope}",
                "evidence_refs": list(action.evidence_refs),
            },
        )
        return record, (receipt,)

    async def approve(self, work_id: str, *, gate_id: str, decision: str) -> WorkRecord:
        record = await self._work_store.load_work(work_id, project_id=PROJECT)
        record = record.model_copy(update={"pending_gate": None})
        await self._work_store.save_work(record)
        events = await self._work_store.read_events(work_id, project_id=PROJECT)
        await self._work_store.append_event(
            WorkEvent(
                id=f"{work_id}:gate-decided:{len(events) + 1}",
                project_id=PROJECT,
                work_id=work_id,
                sequence=len(events) + 1,
                event_type=WorkEventType.GATE_DECIDED,
                actor_type="human",
                actor_ref="arda",
                payload_json={"gate_id": gate_id, "decision": decision},
                created_at=NOW,
            )
        )
        return record

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
        if work_id in self.deliver_sink_versions:
            profile_context["report"] = {
                "pending_sink_version": self.deliver_sink_versions[work_id],
                "deliver_action": self.deliver_action,
            }
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

    async def _open_gate(self, task, record: WorkRecord, gate_id: str) -> WorkRecord:
        record = record.model_copy(update={"pending_gate": gate_id})
        await self._work_store.save_work(record)
        events = await self._work_store.read_events(record.work_id, project_id=task.project_id)
        action = ActionRequest(
            project_id=task.project_id,
            action="merge",
            work_id=record.work_id,
            risk="medium",
            reversibility=Reversibility.COMPENSATABLE,
            scope="https://github.com/o/r/pull/7",
            evidence_refs=("pr://7",),
            rollback="revert_pull_request",
            post_check="merged_sha_read_back",
        )
        await self._work_store.append_event(
            WorkEvent(
                id=f"{record.work_id}:gate-requested:{len(events) + 1}",
                project_id=task.project_id,
                work_id=record.work_id,
                sequence=len(events) + 1,
                event_type=WorkEventType.GATE_REQUESTED,
                actor_type="system",
                actor_ref="test",
                payload_json={
                    "gate_id": gate_id,
                    "question": "Approve merge of PR #7.",
                    "action": action.model_dump(mode="json"),
                    "evidence_refs": ("pr://7",),
                },
                created_at=NOW,
            )
        )
        return record


def _plan_result(attempt_id: str = "plan") -> TaskPlanResult:
    return TaskPlanResult(
        attempt_id=attempt_id,
        steps=tuple(PlanStep.model_validate(step) for step in STEPS),
        acceptance_matrix=tuple(MatrixItem.model_validate(item) for item in MATRIX),
    )


class RecordingDecisionChannel:
    name = "recording"

    def __init__(self, name: str = "recording") -> None:
        self.name = name
        self.calls = []

    async def notify(self, decision):
        self.calls.append(decision)
        return f"recording:{decision.task_id}:{decision.attention_id}:{len(self.calls)}"


class _AngryChannel:
    name = "angry"

    def __init__(self) -> None:
        self.calls = 0

    async def notify(self, decision):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("webhook 503")
        return f"angry:{decision.attention_id}"


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


async def _seed(stores, tmp_path, *, plan_auto: bool = True, origin: TaskOrigin = TaskOrigin.HUMAN):
    from sagewai.artifacts.object_store import LocalArtifactStore

    task_store, work_store = stores
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    service = TaskService(store=task_store, artifact_store=artifacts)
    task, record = await service.create(
        "Implement the retry queue in the payments service repository with a failing test first "
        "and open a pull request when the deterministic verification command passes.",
        project_id=PROJECT,
        origin=origin,
        created_by="arda",
        now=NOW,
    )
    if plan_auto:
        task = task.model_copy(
            update={"authority": task.authority.model_copy(update={"plan": GateMode.AUTO})}
        )
    runner = FakeProfileRunner(work_store, plan_result=_plan_result())
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runners=lambda _task: runner,
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


def _work_event(work_id: str, sequence: int, event_type, payload) -> WorkEvent:
    return WorkEvent(
        id=f"{work_id}:{sequence}",
        project_id=PROJECT,
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=NOW,
    )


def _software_target(repository: Path) -> SoftwareTarget:
    return SoftwareTarget(
        owner="octocat",
        repo="hello-world",
        repository_path=str(repository),
        default_branch="main",
        verification_image="sha256:" + "b" * 64,
    )


def _merge_action_payload(work_id: str) -> dict:
    return ActionRequest(
        project_id=PROJECT,
        action="merge",
        work_id=work_id,
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="https://github.com/octocat/hello-world/pull/7",
        evidence_refs=("https://github.com/octocat/hello-world/issues/42",),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    ).model_dump(mode="json")


def _blocked_merge(
    work_id: str,
    merged_sha: str | None,
    *,
    action: ActionRequest | None = None,
    issue_url: str | None = "https://github.com/octocat/hello-world/issues/42",
) -> tuple[WorkEvent, ...]:
    return (
        _work_event(
            work_id,
            1,
            WorkEventType.GATE_REQUESTED,
            {
                "gate_id": f"merge:{work_id}:7",
                "question": "Approve merge of PR #7.",
                "action": (
                    action.model_dump(mode="json")
                    if action is not None
                    else _merge_action_payload(work_id)
                ),
                "evidence_refs": [],
            },
        ),
        _work_event(
            work_id,
            2,
            WorkEventType.WORK_BLOCKED,
            {
                "reason": "merge_post_check_failed",
                "decision_request": "merge response SHA conflicts with GitHub read-back",
                "merged_sha": merged_sha,
                "issue_url": issue_url,
                "evidence_refs": ["https://github.com/octocat/hello-world/pull/7"],
            },
        ),
    )


async def _rollback_ready(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
    *,
    action_factory=None,
    issue_url: str | None = "https://github.com/octocat/hello-world/issues/42",
):
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    repository, merged_sha = merged_repository
    github = RecordingGitHub()
    github.labeled_issues = (github.issue,)
    coordinator._rollbacks = RollbackExecutor(
        github_factory=lambda _scope: github,
        worktrees=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
    )
    monkeypatch.setattr(
        coordinator,
        "_load",
        _fixed_task(task_store, task.model_copy(update={"target": _software_target(repository)})),
    )
    runner.statuses["s1"] = "WORK_BLOCKED"
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    work_id = runner.started[0]
    action = action_factory(work_id) if action_factory is not None else None
    await work_store.append_events(
        _blocked_merge(work_id, merged_sha, action=action, issue_url=issue_url)
    )
    record = await _drive_to_rest(coordinator, record, epoch)
    service = TaskService(store=task_store, artifact_store=LocalArtifactStore(root=tmp_path))
    return record, coordinator, github, work_id, service


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
    assert TaskEventType.ASSESSMENT_RECORDED in types
    assert TaskEventType.BASE_ADVANCED in types
    assert TaskEventType.BUDGET_RECORDED in types
    assert types[-1] is TaskEventType.TASK_STATUS_CHANGED
    assert types.index(TaskEventType.CYCLE_STARTED) < types.index(TaskEventType.STEP_WORK_STARTED)
    assert types.index(TaskEventType.BUDGET_RECORDED) < types.index(TaskEventType.CYCLE_COMPLETED)
    assert len(runner.ledgers) == 4
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
    assert runner.assessed == [(1, 1, "c" * 40)]


@pytest.mark.asyncio
async def test_assessment_receives_the_latest_base_advanced_sha(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.merged_shas["w-s1-1"] = "b" * 40
    runner.merged_shas["w-s2-2"] = "d" * 40
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)

    record = await _drive_to_rest(coordinator, record, epoch)

    assert record.status is TaskStatus.COMPLETE
    assert runner.assessed == [(1, 1, "d" * 40)]


@pytest.mark.asyncio
async def test_a_completed_task_projects_telemetry_without_raising(
    stores, tmp_path, monkeypatch
) -> None:
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
        project_selections={},
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
    presented = next(
        event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    )
    assert presented.payload_json["urgency"] == "now"
    assert set(presented.payload_json) == {
        "channel",
        "ref",
        "attention_id",
        "urgency",
        "due_at",
        "summary",
        "evidence_refs",
    }


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
async def test_a_stale_lease_epoch_never_reaches_a_side_effect(
    stores, tmp_path, monkeypatch
) -> None:
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
async def test_replay_after_create_issue_uses_the_existing_issue(
    stores, tmp_path, monkeypatch
) -> None:
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
    superseded = [
        event for event in events if event.event_type is TaskEventType.STEP_WORK_SUPERSEDED
    ]
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
    monkeypatch.setattr(coordinator, "_static_channels", (channel,))

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
    presented = next(
        event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    )
    assert record.pending_gate == "merge:w1:7"
    assert record.attention_owner.value == "user"
    assert set(gate.payload_json) == {
        "gate_id",
        "question",
        "action",
        "work_id",
        "attention_id",
        "decided_by",
    }
    assert gate.payload_json["action"] == action.model_dump(mode="json")
    assert gate.payload_json["decided_by"] == "work"
    assert gate.payload_json["work_id"] == work_id
    assert set(presented.payload_json) == {
        "channel",
        "ref",
        "attention_id",
        "urgency",
        "due_at",
        "summary",
        "evidence_refs",
    }
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
    monkeypatch.setattr(coordinator, "_static_channels", (channel,))
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
async def test_a_failing_channel_retries_on_the_next_tick(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    angry = _AngryChannel()
    coordinator._static_channels = (angry, ConsoleDecisionChannel())
    runner.plan_error = PlanningFailedError("planner unavailable")

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    presented = [
        event
        for event in await task_store.read_events(task.id, project_id=PROJECT)
        if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    ]
    assert [event.payload_json["channel"] for event in presented] == ["console"]
    attention_id = presented[0].payload_json["attention_id"]
    assert record.status is TaskStatus.BLOCKED

    entries = await coordinator._present(
        task,
        record,
        attention_id=attention_id,
        summary="retry",
        urgency="now",
    )
    assert angry.calls == 2
    assert [entry[1]["channel"] for entry in entries] == ["angry"]


@pytest.mark.asyncio
async def test_needs_you_items_carry_a_due_time(stores, tmp_path) -> None:
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    coordinator._static_channels = (channel,)

    entries = await coordinator._present(
        task, record, attention_id="gate:1", summary="Approve merge", urgency="today"
    )

    due = datetime.fromisoformat(entries[0][1]["due_at"])
    assert timedelta(hours=23) < due - coordinator._now() <= timedelta(hours=24)
    assert channel.calls[0].due_at == due


@pytest.mark.asyncio
async def test_today_needs_you_items_present_to_the_first_channel_only(stores, tmp_path) -> None:
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    first = RecordingDecisionChannel("first")
    second = RecordingDecisionChannel("second")
    coordinator._static_channels = (first, second)

    entries = await coordinator._present(
        task, record, attention_id="gate:1", summary="Approve merge", urgency="today"
    )

    assert [entry[1]["channel"] for entry in entries] == ["first"]
    assert [call.attention_id for call in first.calls] == ["gate:1"]
    assert second.calls == []


@pytest.mark.asyncio
async def test_today_needs_you_falls_through_when_the_first_channel_fails(stores, tmp_path) -> None:
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    first = _AngryChannel()
    second = RecordingDecisionChannel("slack_webhook")
    coordinator._static_channels = (first, second, ConsoleDecisionChannel())

    entries = await coordinator._present(
        task, record, attention_id="gate:1", summary="Approve merge", urgency="today"
    )

    assert first.calls == 1
    assert [call.attention_id for call in second.calls] == ["gate:1"]
    assert [entry[1]["channel"] for entry in entries] == ["slack_webhook"]


@pytest.mark.asyncio
async def test_needs_you_uses_an_open_clarification_deadline(stores, tmp_path, monkeypatch) -> None:
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    coordinator._static_channels = (channel,)
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
    asked = [
        event for event in events if event.event_type is TaskEventType.CLARIFICATION_REQUESTED
    ][-1]
    deadline = datetime.fromisoformat(asked.payload_json["deadline_at"])

    entries = await coordinator._present(
        task, record, attention_id="clarify:1", summary="Need an answer", urgency="today"
    )

    due = datetime.fromisoformat(entries[0][1]["due_at"])
    assert record.status is TaskStatus.CLARIFYING
    assert due == deadline
    assert channel.calls[0].due_at == deadline
    assert deadline - NOW == timedelta(hours=4)


@pytest.mark.asyncio
async def test_a_block_hands_the_assessment_gaps_to_the_channel(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    coordinator._static_channels = (channel,)
    task = task.model_copy(update={"budget": Budget(max_replans=0)})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.assessor_verdict = "replan"
    runner.assessor_gaps = (
        AssessmentGap(
            statement="step s1 did not reach an accepted outcome",
            severity="high",
            suggested_step="s1",
        ),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert record.status is TaskStatus.BLOCKED
    assert channel.calls[-1].evidence_refs == (
        "step s1 did not reach an accepted outcome (suggested step: s1)",
    )
    assert "did not reach an accepted outcome" in channel.calls[-1].summary


@pytest.mark.asyncio
async def test_a_failed_merge_post_check_offers_a_rollback_a_human_allows(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores
    record, coordinator, github, work_id, service = await _rollback_ready(
        stores, tmp_path, monkeypatch, merged_repository
    )
    assert record.pending_gate == f"rollback:{work_id}"

    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    kinds = [event.event_type for event in events]
    assert (
        kinds.index(TaskEventType.ACTION_INTENT_RECORDED)
        < kinds.index(TaskEventType.ACTION_RESULT_RECORDED)
        < kinds.index(TaskEventType.OBSERVATION_RECORDED)
    )
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    assert result.payload_json["action_id"] == f"revert:{work_id}:7"
    assert result.payload_json["status"] == "succeeded"
    merged_sha = merged_repository[1]
    assert github.pull_requests[0]["head"] == f"sagewai/revert-7-{merged_sha[:12]}"
    assert len(github.merges) == 1
    assert record.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_a_rollback_whose_batch_is_lost_asks_instead_of_reverting_twice(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores
    record, coordinator, github, work_id, service = await _rollback_ready(
        stores, tmp_path, monkeypatch, merged_repository
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )

    _lose_the_batch(monkeypatch, kind="rollback_work")
    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=record.lease_epoch)
    record = (await task_store.load(task.id, project_id=PROJECT))[1]
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    assert len(github.merges) == 1
    events = await task_store.read_events(task.id, project_id=PROJECT)
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert result.payload_json["status"] == "blocked"
    assert observation.payload_json["check"] == "rollback_receipt"
    assert observation.payload_json["passed"] is None
    assert (
        observation.payload_json["detail"]
        == "the rollback may have run before a crash; confirm the outcome on GitHub"
    )


@pytest.mark.asyncio
async def test_denying_a_rollback_gate_blocks_without_running_actions(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores
    record, coordinator, github, work_id, service = await _rollback_ready(
        stores, tmp_path, monkeypatch, merged_repository
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)

    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="deny",
        actor_ref="arda",
    )
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    assert record.status is TaskStatus.BLOCKED
    assert record.pending_gate is None
    assert not any(
        event.event_type
        in {TaskEventType.ACTION_INTENT_RECORDED, TaskEventType.ACTION_RESULT_RECORDED}
        for event in events
    )
    assert github.merges == []


@pytest.mark.asyncio
async def test_a_refused_rollback_records_failed_action_and_blocks(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores
    record, coordinator, _github, work_id, service = await _rollback_ready(
        stores, tmp_path, monkeypatch, merged_repository
    )

    class RefusingRollbackExecutor(RollbackExecutor):
        async def run(self, *args, **kwargs):
            raise RollbackRefusedError("revert conflicts")

    coordinator._rollbacks = RefusingRollbackExecutor(
        github_factory=lambda _scope: RecordingGitHub()
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    receipt = next(
        event
        for event in events
        if event.event_type is TaskEventType.COMMAND_RECEIPT
        and event.payload_json["kind"] == "rollback_work"
    )
    intent = next(
        event for event in events if event.event_type is TaskEventType.ACTION_INTENT_RECORDED
    )
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    message = [event for event in events if event.event_type is TaskEventType.TASK_MESSAGE][-1]
    assert receipt.sequence < intent.sequence < result.sequence < observation.sequence
    assert receipt.payload_json["payload"] == {"kind": "rollback_work", "work_id": work_id}
    assert intent.payload_json["action_id"] == f"revert:{work_id}:7"
    assert result.payload_json["action_id"] == f"revert:{work_id}:7"
    assert result.payload_json["status"] == "failed"
    assert observation.payload_json == {
        "work_id": work_id,
        "action_id": f"revert:{work_id}:7",
        "check": "rollback_refused",
        "passed": False,
        "detail": "revert conflicts",
        "evidence_refs": [],
    }
    assert "revert conflicts" in message.payload_json["text"]
    assert record.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_a_pre_receipt_rollback_refusal_records_result_and_does_not_repeat(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores

    def malformed_merge(work_id: str) -> ActionRequest:
        return ActionRequest(
            project_id=PROJECT,
            action="merge",
            work_id=work_id,
            risk="medium",
            reversibility=Reversibility.COMPENSATABLE,
            scope="merge:not-a-github-pull-request",
            evidence_refs=("work://w1",),
            rollback="revert_pull_request",
            post_check="merged_sha_read_back",
        )

    record, coordinator, _github, work_id, service = await _rollback_ready(
        stores,
        tmp_path,
        monkeypatch,
        merged_repository,
        action_factory=malformed_merge,
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert result.payload_json["work_id"] == work_id
    assert result.payload_json["action_id"].startswith(f"revert:{work_id}:")
    assert result.payload_json["status"] == "failed"
    assert observation.payload_json["check"] == "rollback_refused"
    assert "not a GitHub pull request URL" in observation.payload_json["detail"]

    result_count = sum(event.event_type is TaskEventType.ACTION_RESULT_RECORDED for event in events)
    record = await TaskWriter(task_store).append(
        record,
        [status_entry(record, TaskStatus.EXECUTING)],
        lease_epoch=record.lease_epoch,
        now=NOW,
    )
    record = await coordinator.drive(record, lease_epoch=record.lease_epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)

    assert record.status is TaskStatus.EXECUTING
    assert (
        sum(event.event_type is TaskEventType.ACTION_RESULT_RECORDED for event in events)
        == result_count
    )


@pytest.mark.asyncio
async def test_an_irreversible_rollback_blocks_without_running_the_executor(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores

    def irreversible(work_id: str) -> ActionRequest:
        return ActionRequest(
            project_id=PROJECT,
            action="deliver",
            work_id=work_id,
            risk="medium",
            reversibility=Reversibility.COMPENSATABLE,
            scope="https://github.com/octocat/hello-world/issues/42",
            evidence_refs=("artifact://report",),
            rollback="delete_comment",
            post_check="comment_read_back",
        )

    record, coordinator, _github, work_id, service = await _rollback_ready(
        stores,
        tmp_path,
        monkeypatch,
        merged_repository,
        action_factory=irreversible,
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)

    class CountingRollbackExecutor(RollbackExecutor):
        calls = 0

        async def run(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("executor should not run")

    executor = CountingRollbackExecutor(github_factory=lambda _scope: RecordingGitHub())
    coordinator._rollbacks = executor
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )
    record = await _drive_to_rest(coordinator, record, record.lease_epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    message = [event for event in events if event.event_type is TaskEventType.TASK_MESSAGE][-1]
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert "irreversible and was not run" in message.payload_json["text"]
    assert result.payload_json["work_id"] == work_id
    assert result.payload_json["action_id"].startswith(f"delete_comment:{work_id}:")
    assert result.payload_json["status"] == "failed"
    assert observation.payload_json["work_id"] == work_id
    assert observation.payload_json["check"] == "rollback_refused"
    assert executor.calls == 0
    assert record.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_allowing_a_rollback_gate_from_blocked_appends_executing_status(
    stores,
    tmp_path,
    monkeypatch,
    merged_repository,  # noqa: F811
) -> None:
    task_store, _work_store = stores
    record, _coordinator, _github, work_id, service = await _rollback_ready(
        stores, tmp_path, monkeypatch, merged_repository
    )
    task, _stored = await task_store.load(record.task_id, project_id=PROJECT)
    record = await TaskWriter(task_store).append(
        record,
        [(TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.BLOCKED.value})],
        lease_epoch=record.lease_epoch,
        now=NOW,
    )
    blocked_sequence = record.last_event_sequence

    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id=f"rollback:{work_id}",
        decision="allow",
        actor_ref="arda",
    )

    events = await task_store.read_events(task.id, project_id=PROJECT)
    new_events = [event for event in events if event.sequence > blocked_sequence]
    assert record.status is TaskStatus.EXECUTING
    assert [event.event_type for event in new_events] == [
        TaskEventType.GATE_DECIDED,
        TaskEventType.TASK_STATUS_CHANGED,
    ]
    assert new_events[-1].payload_json["status"] == TaskStatus.EXECUTING.value


@pytest.mark.asyncio
async def test_a_failed_merge_post_check_without_a_merge_sha_blocks_without_rollback_gate(
    stores,
    tmp_path,
    monkeypatch,
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(
        coordinator,
        "_load",
        _fixed_task(task_store, task.model_copy(update={"target": _software_target(tmp_path)})),
    )
    runner.statuses["s1"] = "WORK_BLOCKED"
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    work_id = runner.started[0]

    await work_store.append_events(_blocked_merge(work_id, None))
    record = await _drive_to_rest(coordinator, record, epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    gates = [
        event.payload_json["gate_id"]
        for event in events
        if event.event_type is TaskEventType.GATE_REQUESTED
    ]
    assert f"rollback:{work_id}" not in gates
    assert record.pending_gate is None
    assert record.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_a_failed_merge_post_check_without_an_issue_url_blocks_without_rollback_gate(
    stores,
    tmp_path,
    monkeypatch,
) -> None:
    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(
        coordinator,
        "_load",
        _fixed_task(task_store, task.model_copy(update={"target": _software_target(tmp_path)})),
    )
    runner.statuses["s1"] = "WORK_BLOCKED"
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    work_id = runner.started[0]

    await work_store.append_events(_blocked_merge(work_id, "c" * 40, issue_url=None))
    record = await _drive_to_rest(coordinator, record, epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    gates = [
        event.payload_json["gate_id"]
        for event in events
        if event.event_type is TaskEventType.GATE_REQUESTED
    ]
    assert f"rollback:{work_id}" not in gates
    assert record.pending_gate is None
    assert record.status is TaskStatus.BLOCKED


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
        profile_runners=lambda _task: runner,
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
    asked = [
        event for event in events if event.event_type is TaskEventType.CLARIFICATION_REQUESTED
    ][-1]
    defaults = await task_store.get_defaults(project_id=PROJECT)
    assert record.status is TaskStatus.CLARIFYING
    assert (
        asked.payload_json["deadline_at"]
        == (NOW + timedelta(seconds=defaults.clarification_deadline_seconds)).isoformat()
    )


@pytest.mark.asyncio
async def test_a_planning_exception_degrades_control_on_the_task(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    coordinator._static_channels = (channel,)
    runner.plan_error = ValueError("verification sandbox image must be digest-pinned")

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.CONTROL_DEGRADED
    assert record.attention_owner is AttentionOwner.USER
    events = await task_store.read_events(task.id, project_id=PROJECT)
    degraded = next(
        event for event in events if event.event_type is TaskEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["command"] == "run_planning"
    assert "digest-pinned" in degraded.payload_json["detail"]
    message = next(
        event
        for event in reversed(events)
        if event.event_type is TaskEventType.TASK_MESSAGE
    )
    assert "digest-pinned" in message.payload_json["text"]
    assert [call.urgency for call in channel.calls] == ["now"]


@pytest.mark.asyncio
async def test_an_unreachable_base_degrades_control_before_planning(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.base_sha_error = RuntimeError("repository unreachable: connection refused")

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.CONTROL_DEGRADED
    events = await task_store.read_events(task.id, project_id=PROJECT)
    degraded = next(
        event for event in events if event.event_type is TaskEventType.CONTROL_DEGRADED
    )
    assert degraded.payload_json["command"] == "run_planning"
    assert "connection refused" in degraded.payload_json["detail"]


@pytest.mark.asyncio
async def test_a_step_exception_still_propagates_for_crash_replay(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    original = runner.create_issue

    async def crash_after_issue(task_, **kwargs):
        url = await original(task_, **kwargs)
        raise RuntimeError(f"crashed after creating {url}")

    runner.create_issue = crash_after_issue
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)

    with pytest.raises(RuntimeError):
        await coordinator.drive(record, lease_epoch=epoch)

    record = (await task_store.load(task.id, project_id=PROJECT))[1]
    assert record.status is not TaskStatus.CONTROL_DEGRADED


@pytest.mark.asyncio
async def test_budget_exhaustion_records_ledger_usage_and_notifies_now(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_static_channels", (channel,))
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
    presented = [
        event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    ][-1]
    assert record.status is TaskStatus.BUDGET_EXHAUSTED
    assert record.attention_owner.value == "user"
    assert budget.payload_json["budget_used"]["usd_reserved"] == "1.00"
    assert presented.payload_json["urgency"] == "now"
    assert channel.calls[-1].urgency == "now"


@pytest.mark.asyncio
async def test_a_defaulted_answer_reaches_the_next_plan_version(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    record = await TaskWriter(task_store).append(
        record,
        [
            (
                TaskEventType.CLARIFICATION_REQUESTED,
                {
                    "questions": [
                        {
                            "id": "difficulty",
                            "text": "Which difficulty axis?",
                            "kind": "text",
                            "options": [],
                            "default": "the plan above",
                            "defaultable": True,
                            "rationale": "",
                            "attention_version": 1,
                        }
                    ],
                    "deadline_at": (NOW + timedelta(hours=4)).isoformat(),
                },
            ),
            (
                TaskEventType.CLARIFICATION_DEFAULTED,
                {"question_id": "difficulty", "answer": "the plan above"},
            ),
        ],
        now=NOW + timedelta(hours=4),
    )
    assert record.status is TaskStatus.PLANNING and record.pending_questions == 0
    amendments: list[tuple[str, ...]] = []
    original_plan = runner.plan

    async def capturing_plan(task_, **kwargs):
        amendments.append(kwargs["amendments"])
        return await original_plan(task_, **kwargs)

    runner.plan = capturing_plan
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    await _drive_to_rest(coordinator, record, epoch)

    assert amendments[0] == ("difficulty: the plan above",)


@pytest.mark.asyncio
async def test_ungated_replan_reenters_planning_and_runs_the_planner_again(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.AUTO)}
    )
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

    async def assess(*args, **kwargs):
        return next(verdicts)

    runner.assess = assess
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
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.REQUIRE)}
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    plan_calls = {"n": 0}
    original_plan = runner.plan

    async def counted_plan(task_, **kwargs):
        plan_calls["n"] += 1
        return await original_plan(task_, **kwargs)

    runner.plan = counted_plan
    runner.assessor_verdict = "replan"
    runner.assessor_gaps = (AssessmentGap(statement="gap", severity="high", suggested_step="s1"),)
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
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.REQUIRE)}
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.assessor_verdict = "replan"
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
    task_store, _ = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    channel = RecordingDecisionChannel()
    monkeypatch.setattr(coordinator, "_static_channels", (channel,))
    task = task.model_copy(update={"budget": task.budget.model_copy(update={"max_replans": 0})})
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.assessor_verdict = "replan"
    runner.assessor_gaps = (
        AssessmentGap(
            statement="deterministic check failed",
            severity="high",
            suggested_step="repair-step",
        ),
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    events = await task_store.read_events(task.id, project_id=PROJECT)
    message = [event for event in events if event.event_type is TaskEventType.TASK_MESSAGE][-1]
    presented = [
        event for event in events if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    ][-1]
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


@pytest.mark.asyncio
async def test_a_task_without_retention_completes_without_pruning(
    stores, tmp_path, monkeypatch
) -> None:
    from sagewai.work.activity import WorkActivityStore

    task_store, work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    assert task.retention_days is None
    pruned: list[dict] = []

    class RecordingActivityStore(WorkActivityStore):
        async def prune(self, **kwargs):
            pruned.append(kwargs)
            return 0

    coordinator._activity_store = RecordingActivityStore(engine=task_store._engine)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    for _ in range(20):
        record = await coordinator.drive(record, lease_epoch=epoch)
        if record.status is TaskStatus.COMPLETE:
            break
    else:
        pytest.fail("the Task never completed")
    assert pruned == []

    task2, record2, runner2, coordinator2 = await _seed(stores, tmp_path)
    retained = task2.model_copy(update={"retention_days": 1})
    coordinator2._activity_store = RecordingActivityStore(engine=task_store._engine)
    monkeypatch.setattr(coordinator2, "_load", _fixed_task(task_store, retained))
    epoch2 = await task_store.claim(task2.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    for _ in range(20):
        record2 = await coordinator2.drive(record2, lease_epoch=epoch2)
        if record2.status is TaskStatus.COMPLETE:
            break
    else:
        pytest.fail("the Task never completed")
    assert len(pruned) == 1 and pruned[0]["project_id"] == PROJECT


@pytest.mark.asyncio
async def test_a_plan_with_a_defaultable_question_is_proposed_and_the_question_recorded(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_now", lambda: NOW)
    runner.plan_result = _plan_result().model_copy(
        update={
            "clarifications": (
                ClarificationQuestion(
                    id="difficulty",
                    text="Which difficulty axis do you prefer?",
                    defaultable=True,
                    default="the plan above",
                ),
            )
        }
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    kinds = [
        event.event_type
        for event in await task_store.read_events(task.id, project_id=PROJECT)
    ]
    assert TaskEventType.PLAN_PROPOSED in kinds
    assert TaskEventType.CLARIFICATION_REQUESTED in kinds
    assert record.status is TaskStatus.PLAN_PROPOSED
    assert record.pending_questions == 1 and record.pending_material_questions == 0
    asked = [
        event
        for event in await task_store.read_events(task.id, project_id=PROJECT)
        if event.event_type is TaskEventType.CLARIFICATION_REQUESTED
    ][-1]
    defaults = await task_store.get_defaults(project_id=PROJECT)
    assert [question["id"] for question in asked.payload_json["questions"]] == ["difficulty"]
    assert (
        asked.payload_json["deadline_at"]
        == (NOW + timedelta(seconds=defaults.clarification_deadline_seconds)).isoformat()
    )
