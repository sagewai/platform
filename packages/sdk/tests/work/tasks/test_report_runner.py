# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The coordinator selects the report ProfileRunner for report Tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.models import SUPERSEDED, ActionRequest, ActionResult, WorkRecord
from sagewai.work.store import WorkStore
from sagewai.work.tasks.actions import DeliveryReceipt, deliver_action
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decisions import ConsoleDecisionChannel
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import (
    GateMode,
    ReportTarget,
    TaskDefaults,
    TaskOrigin,
    TaskStatus,
)
from sagewai.work.tasks.plan import MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.report import ReportProfileRunner, step_ref
from sagewai.work.tasks.service import TaskService
from sagewai.work.tasks.store import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_coordinator import (
    PROJECT,
    FakeProfileRunner,
    _drive_to_rest,
    _seed,
)
from tests.work.tasks.test_store import _task as _software_task

_ACTION_EVENTS = frozenset(
    {
        TaskEventType.ACTION_INTENT_RECORDED,
        TaskEventType.ACTION_RESULT_RECORDED,
        TaskEventType.OBSERVATION_RECORDED,
    }
)
_REPORT_BRIEF = (
    "Every week research the latest vendor announcements and write a sourced summary report."
)


def _report_task(task_id: str = "task-report"):
    return _software_task(task_id, project_id=PROJECT).model_copy(
        update={"profile": "report", "target": ReportTarget(required_sections=("Summary",))}
    )


def _report_plan() -> TaskPlanResult:
    return TaskPlanResult(
        attempt_id="report-plan",
        steps=(
            PlanStep(
                id="report",
                title="Compose the report",
                goal="Read the declared sources and compose the report",
                allowed_scope=("report.md",),
                acceptance_criteria=(
                    {
                        "statement": "the report is composed, verified, reviewed, and delivered",
                        "verification_kind": "profile",
                    },
                ),
                risk="low",
                domain="report",
            ),
        ),
        acceptance_matrix=(
            MatrixItem(
                id="grounded",
                statement="every claim in the report cites a source snapshot",
                verification_kind="policy",
            ),
        ),
    )


class FakeReportProfileRunner(FakeProfileRunner):
    """Report runner fake with scratch refs and a delivery step."""

    async def base_sha(self, task):
        return None

    async def plan(self, task, *, cycle, plan_version, base_sha, brief_text, amendments):
        assert base_sha is None
        return await super().plan(
            task,
            cycle=cycle,
            plan_version=plan_version,
            base_sha=self.head,
            brief_text=brief_text,
            amendments=amendments,
        )

    async def find_issue(self, task, *, cycle, step):
        return self.issues.get(step.id)

    async def create_issue(self, task, *, cycle, step):
        url = step_ref(task, cycle=cycle, step=step)
        self.issues[step.id] = url
        self.created_issues.append((step.id, url))
        return url

    async def start(self, task, *, cycle, step, issue_url, base_sha, evidence_refs=()):
        work_id = f"{task.id}:report:{cycle}:{step.id}"
        self.started.append(work_id)
        self.evidence.append(tuple(evidence_refs))
        action = deliver_action(
            task.project_id,
            work_id=work_id,
            scope=f"artifact://{work_id}",
            evidence_refs=(issue_url,),
            rollback=None,
        )
        record = WorkRecord(
            work_id=work_id,
            project_id=task.project_id,
            source_ref=issue_url,
            profile="report",
            status=self.statuses.pop(step.id, "READY_TO_DELIVER"),
            contract_version=1,
            active_run_id=None,
            pending_gate=None,
            profile_context={
                "task_id": task.id,
                "cycle": cycle,
                "report": {
                    "pending_sink_version": 1,
                    "deliver_action": action.model_dump(mode="json"),
                },
            },
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await self._work_store.save_work(record)
        return record

    async def deliver(self, task, *, work_id: str, sink_version: int):
        self.delivered.append((work_id, sink_version))
        record = await self._work_store.load_work(work_id, project_id=task.project_id)
        action = ActionRequest.model_validate(record.profile_context["report"]["deliver_action"])
        record = record.model_copy(update={"status": "COMPLETE"})
        await self._work_store.save_work(record)
        now = datetime.now(timezone.utc)
        receipt = DeliveryReceipt(
            action=action,
            result=ActionResult(
                project_id=task.project_id,
                action_id=f"deliver:{work_id}:{sink_version}",
                status="succeeded",
                external_ref=action.scope,
                evidence_refs=action.evidence_refs,
                started_at=now,
                completed_at=now,
            ),
            observation={
                "action_id": f"deliver:{work_id}:{sink_version}",
                "check": action.post_check,
                "passed": True,
                "detail": f"delivered {action.scope}",
                "evidence_refs": list(action.evidence_refs),
            },
        )
        return record, (receipt,)


@pytest.fixture
async def stores(dialect_engine):  # noqa: F811
    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id=PROJECT, target=_software_task().target),
        expected_revision=0,
    )
    return task_store, work_store


async def _seed_report(stores, tmp_path):
    task_store, work_store = stores
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    service = TaskService(store=task_store, artifact_store=artifacts)
    task, record = await service.create(
        _REPORT_BRIEF,
        project_id=PROJECT,
        origin=TaskOrigin.HUMAN,
        created_by="arda",
        target=ReportTarget(required_sections=("Summary",)),
        now=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
    )
    events = await task_store.read_events(task.id, project_id=PROJECT)
    for event in events:
        if event.event_type is TaskEventType.CLARIFICATION_REQUESTED:
            for question in event.payload_json["questions"]:
                record = await service.answer_clarification(
                    task.id,
                    project_id=PROJECT,
                    question_id=question["id"],
                    attention_version=1,
                    answer="https://example.com/news",
                    actor_ref="arda",
                    now=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
                )
    task = task.model_copy(
        update={"authority": task.authority.model_copy(update={"plan": GateMode.AUTO})}
    )
    runner = FakeReportProfileRunner(work_store, plan_result=_report_plan())
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runners=lambda _task: runner,
        artifact_store=artifacts,
        decision_channels=(ConsoleDecisionChannel(),),
    )
    return task, record, runner, coordinator


def test_target_rejects_a_software_task() -> None:
    with pytest.raises(ValueError, match="is not a report Task"):
        ReportProfileRunner._target(_software_task(project_id=PROJECT))


@pytest.mark.asyncio
async def test_find_work_skips_non_live_report_records(stores) -> None:
    _task_store, work_store = stores
    task = _report_task()
    runner = ReportProfileRunner(work_store=work_store)
    issue_url = "report://task-report/1/report"
    now = datetime.now(timezone.utc)
    for work_id, profile, status in (
        ("w-superseded", "report", SUPERSEDED),
        ("w-software", "software", "IMPLEMENTING"),
        ("w-excluded", "report", "READY_TO_DELIVER"),
        ("w-live", "report", "READY_TO_DELIVER"),
    ):
        await work_store.save_work(
            WorkRecord(
                work_id=work_id,
                project_id=task.project_id,
                source_ref=issue_url,
                profile=profile,
                status=status,
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context={"task_id": task.id},
                created_at=now,
                updated_at=now,
            )
        )

    found = await runner.find_work(task, issue_url=issue_url, exclude="w-excluded")

    assert found is not None
    assert found.work_id == "w-live"


@pytest.mark.asyncio
async def test_report_base_sha_and_is_merged_are_repository_neutral(stores) -> None:
    _task_store, work_store = stores
    task = _report_task()
    runner = ReportProfileRunner(work_store=work_store)

    assert await runner.base_sha(task) is None
    assert await runner.is_merged(task, work_id="w-live") is False


@pytest.mark.asyncio
async def test_report_runner_threads_harness_backends_to_the_stack_builder(
    stores,
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    _task_store, work_store = stores
    calls = []

    async def fake_stack(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(activity_sink=object())

    monkeypatch.setattr("sagewai.work.tasks.report.build_report_stack", fake_stack)
    sandbox = object()
    connection_store = object()
    credentials = object()
    secret_provider = object()
    runner = ReportProfileRunner(
        work_store=work_store,
        engine=dialect_engine,
        sandbox=sandbox,
        connection_store=connection_store,
        credentials=credentials,
        secret_provider=secret_provider,
    )

    await runner._stack(_report_task())

    assert calls[0]["sandbox"] is sandbox
    assert calls[0]["connection_store"] is connection_store
    assert calls[0]["credentials"] is credentials
    assert calls[0]["secret_provider"] is secret_provider


@pytest.mark.asyncio
async def test_a_report_task_plans_composes_delivers_and_completes(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed_report(stores, tmp_path)

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    events = await task_store.read_events(task.id, project_id=PROJECT)
    assert record.status is TaskStatus.SCHEDULED
    assert runner.delivered == [(runner.started[0], 1)]
    assert [event.event_type for event in events if event.event_type in _ACTION_EVENTS] == [
        TaskEventType.ACTION_INTENT_RECORDED,
        TaskEventType.ACTION_RESULT_RECORDED,
        TaskEventType.OBSERVATION_RECORDED,
    ]
    assert not any(event.event_type is TaskEventType.REPOSITORY_LEASE_ACQUIRED for event in events)
    started = next(event for event in events if event.event_type is TaskEventType.STEP_WORK_STARTED)
    assert started.payload_json["issue_url"].startswith("report://")
    assert started.payload_json["base_sha"] is None


@pytest.mark.asyncio
async def test_the_selector_sends_each_task_to_its_own_runner(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, work_store = stores
    software_task, software_record, software_runner, _ = await _seed(stores, tmp_path)
    report_task, report_record, report_runner, _ = await _seed_report(stores, tmp_path)
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runners=lambda task: report_runner if task.profile == "report" else software_runner,
        artifact_store=LocalArtifactStore(root=tmp_path / "objects"),
        decision_channels=(ConsoleDecisionChannel(),),
    )

    async def _load(task_id: str, project_id: str):
        loaded = await task_store.load(task_id, project_id=project_id)
        _task, loaded_record = loaded
        selected = software_task if task_id == software_task.id else report_task
        return selected, loaded_record

    monkeypatch.setattr(coordinator, "_load", _load)

    for record in (software_record, report_record):
        epoch = await task_store.claim(
            record.task_id, project_id=PROJECT, owner="r", ttl_seconds=90
        )
        await _drive_to_rest(coordinator, record, epoch)

    assert software_runner.started and report_runner.started
    assert software_runner.issues.keys() == {"s1", "s2"}
    assert all(url.startswith("report://") for url in report_runner.issues.values())
