# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""GitHub tracking issue decision channel."""

from __future__ import annotations

from datetime import timedelta

import pytest

from sagewai.work.profiles.software.github import GitHubIssue
from sagewai.work.store import WorkStore
from sagewai.work.tasks.assessment import AssessmentGap
from sagewai.work.tasks.channels import GitHubIssueDecisionChannel
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.decisions import ConsoleDecisionChannel
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import (
    Authority,
    GateMode,
    ReportTarget,
    Schedule,
    Sink,
    Task,
    TaskDefaults,
    TaskKind,
    TaskRecord,
    TaskStatus,
)
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_coordinator import (
    PROJECT,
    FakeProfileRunner,
    RecordingDecisionChannel,
    _drive_to_rest,
    _fixed_task,
    _seed,
)
from tests.work.tasks.test_software_kernel import RecordingGitHub
from tests.work.tasks.test_store import NOW, _record, _task


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


async def _store_task(task_store: TaskStore, task: Task) -> TaskRecord:
    return await task_store.create(
        task,
        events=(
            TaskEvent(
                id=f"{task.id}:created",
                project_id=task.project_id,
                task_id=task.id,
                sequence=1,
                event_type=TaskEventType.TASK_CREATED,
                actor_type="system",
                actor_ref="test",
                payload_json={"title": task.title},
                created_at=NOW,
            ),
        ),
        record=_record(task),
    )


async def _seed_report(
    stores, *, sinks: tuple[Sink, ...] = ()
) -> tuple[Task, TaskRecord, TaskCoordinator]:
    task_store, work_store = stores
    base = _task("report-task", project_id=PROJECT)
    values = base.model_dump(mode="python")
    values.update(
        {
            "title": "Research competitor positioning",
            "brief_summary": "Research competitor positioning",
            "template_id": "scheduled_research_report",
            "template_version": "2",
            "profile": "report",
            "target": ReportTarget(sources=(), sinks=sinks),
        }
    )
    task = Task.model_validate(values)
    record = await _store_task(task_store, task)
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runner=FakeProfileRunner(work_store),
        decision_channels=(ConsoleDecisionChannel(),),
    )
    return task, record, coordinator


async def _seed_task_with_override(
    stores, *, tracking_issue_url: str
) -> tuple[Task, TaskRecord, TaskCoordinator]:
    task_store, work_store = stores
    base = _task("override-task", project_id=PROJECT)
    task = Task.model_validate(
        {**base.model_dump(mode="python"), "tracking_issue_url": tracking_issue_url}
    )
    record = await _store_task(task_store, task)
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runner=FakeProfileRunner(work_store),
        decision_channels=(ConsoleDecisionChannel(),),
    )
    return task, record, coordinator


def _issue(number: int, *, owner: str = "octocat", repo: str = "hello-world") -> GitHubIssue:
    return GitHubIssue(
        project_id=PROJECT,
        owner=owner,
        repo=repo,
        number=number,
        url=f"https://github.com/{owner}/{repo}/issues/{number}",
        title="Sagewai Task",
        body="Recovered tracking issue",
        default_branch="main",
    )


def _comment_bodies(github: RecordingGitHub, needle: str) -> list[str]:
    return [body for _url, body in github.comments if needle in body]


@pytest.mark.asyncio
async def test_the_first_item_creates_the_tracking_issue_and_the_rest_comment(
    stores, tmp_path
) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    github = RecordingGitHub()
    channel = GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github)
    coordinator._channels = (channel,)

    first = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )
    record = await TaskWriter(task_store).append(record, first)
    second = await coordinator._present(
        task, record, attention_id="a2", summary="Merge approval", urgency="today"
    )
    record = await TaskWriter(task_store).append(record, second)

    assert len(github.created) == 1
    assert github.created[0]["title"].endswith(task.title)
    assert github.created[0]["labels"] == (f"sagewai-task:{task.id}",)
    assert task.id in github.created[0]["body"]
    assert record.tracking_issue_url == github.labeled_issues[0].url
    issue_url = github.labeled_issues[0].url
    assert [url for url, _body in github.comments] == [issue_url, issue_url]
    assert github.comments[1][1].splitlines()[0] == "**Merge approval**"
    assert "urgency today, due " in github.comments[1][1]
    assert [entry[0] for entry in first] == [
        TaskEventType.TRACKING_ISSUE_RECORDED,
        TaskEventType.NOTIFICATION_PRESENTED,
    ]
    assert [entry[0] for entry in second] == [TaskEventType.NOTIFICATION_PRESENTED]


@pytest.mark.asyncio
async def test_an_existing_unrecorded_tracking_issue_is_reused(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)

    class LabelRecordingGitHub(RecordingGitHub):
        def __init__(self) -> None:
            super().__init__()
            self.label_calls = []

        async def list_labeled_issues(self, *, owner, repo, label):
            self.label_calls.append((owner, repo, label))
            return await super().list_labeled_issues(owner=owner, repo=repo, label=label)

    github = LabelRecordingGitHub()
    existing = _issue(99)
    github.labeled_issues = (existing,)
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    entries = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )
    record = await TaskWriter(task_store).append(record, entries)

    assert github.created == []
    assert github.comments[0][0] == existing.url
    assert record.tracking_issue_url == existing.url
    assert github.label_calls == [("o", "r", f"sagewai-task:{task.id}")]
    assert entries[0] == (TaskEventType.TRACKING_ISSUE_RECORDED, {"url": existing.url})


@pytest.mark.asyncio
async def test_a_failed_tracking_comment_does_not_leak_the_established_issue(
    stores, tmp_path
) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    github = RecordingGitHub()
    first_issue = _issue(77)
    second_issue = _issue(88)
    github.labeled_issues = (first_issue,)
    github.fail_comment_once = True
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    assert (
        await coordinator._present(
            task, record, attention_id="a1", summary="Plan proposed", urgency="today"
        )
        == []
    )
    record = await TaskWriter(task_store).append(
        record, [(TaskEventType.TRACKING_ISSUE_RECORDED, {"url": second_issue.url})]
    )
    entries = await coordinator._present(
        task, record, attention_id="a2", summary="Merge approval", urgency="today"
    )

    assert github.comments[-1][0] == second_issue.url
    assert all(entry[0] is not TaskEventType.TRACKING_ISSUE_RECORDED for entry in entries)


@pytest.mark.asyncio
async def test_tracking_channel_is_not_limited_by_non_now_channel_position(
    stores, tmp_path
) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    console = RecordingDecisionChannel("console")
    slack = RecordingDecisionChannel("slack_webhook")
    github = RecordingGitHub()
    tracking = GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github)
    coordinator._channels = (console, tracking, slack)

    entries = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )

    assert [call.attention_id for call in console.calls] == ["a1"]
    assert [url for url, _body in github.comments] == [github.labeled_issues[0].url]
    assert slack.calls == []
    assert [entry[0] for entry in entries] == [
        TaskEventType.NOTIFICATION_PRESENTED,
        TaskEventType.TRACKING_ISSUE_RECORDED,
        TaskEventType.NOTIFICATION_PRESENTED,
    ]
    presented = [
        entry[1]["channel"] for entry in entries if entry[0] is TaskEventType.NOTIFICATION_PRESENTED
    ]
    assert presented == [
        "console",
        "github_issue",
    ]


@pytest.mark.asyncio
async def test_a_report_task_without_an_issue_sink_presents_nothing(stores) -> None:
    task_store, _work_store = stores
    task, record, coordinator = await _seed_report(stores)
    github = RecordingGitHub()
    built = 0

    def github_factory(_scope):
        nonlocal built
        built += 1
        return github

    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=github_factory),
    )

    entries = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )

    assert entries == []
    assert built == 0
    assert github.created == [] and github.comments == []
    assert await task_store.record_command(
        task_id=task.id,
        project_id=task.project_id,
        command_id="notify:github_issue:a1",
        payload={"probe": True},
    )


@pytest.mark.asyncio
async def test_a_task_tracking_issue_override_is_used_without_projection(stores) -> None:
    task_store, _work_store = stores
    override_url = "https://github.com/acme/tracker/issues/7"
    task, record, coordinator = await _seed_task_with_override(
        stores, tracking_issue_url=override_url
    )
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    entries = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )

    assert github.created == []
    assert github.comments[0][0] == override_url
    assert all(entry[0] is not TaskEventType.TRACKING_ISSUE_RECORDED for entry in entries)


@pytest.mark.asyncio
async def test_a_report_issue_sink_selects_the_tracking_issue_repository(stores) -> None:
    task_store, _work_store = stores
    task, record, coordinator = await _seed_report(
        stores,
        sinks=(
            Sink(
                kind="github_issue", issue_url="https://github.com/acme/reports/issues/5", version=2
            ),
        ),
    )
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    entries = await coordinator._present(
        task, record, attention_id="a1", summary="Plan proposed", urgency="today"
    )

    assert github.created[0]["owner"] == "acme"
    assert github.created[0]["repo"] == "reports"
    assert entries[0][1]["url"] == github.labeled_issues[0].url


@pytest.mark.asyncio
async def test_a_plan_gate_is_presented_to_the_tracking_issue_once(stores, tmp_path) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path, plan_auto=False)
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.PLAN_PROPOSED
    assert len(_comment_bodies(github, "**Approve the 2-step plan.**")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_a_replan_gate_is_presented_to_the_tracking_issue_once(
    stores, tmp_path, monkeypatch
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={"authority": Authority(plan=GateMode.AUTO, replan=GateMode.REQUIRE)}
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.assessor_verdict = "replan"
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.pending_gate == f"replan:{task.id}:2"
    assert len(_comment_bodies(github, "**assessment requested a re-plan**")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_cycle_start_tracks_the_accepted_plan_once(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.COMPLETE
    assert len(_comment_bodies(github, "Accepted plan v1")) == 1
    assert len(_comment_bodies(github, "- s1: Step s1")) == 1
    assert len(_comment_bodies(github, "Pull request: work://w-s1-1")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_cycle_start_tracks_a_replanned_v2_once(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    task = task.model_copy(
        update={
            "kind": TaskKind.SCHEDULED,
            "schedule": Schedule(cron="0 9 * * 1", timezone="Europe/Berlin"),
            "authority": Authority.for_kind(TaskKind.SCHEDULED),
        }
    )
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    result = runner.plan_result
    plan_payload = {
        "steps": [step.model_dump(mode="json") for step in result.steps],
        "acceptance_matrix": [item.model_dump(mode="json") for item in result.acceptance_matrix],
    }
    record = await TaskWriter(task_store).append(
        record,
        [
            (TaskEventType.PLAN_PROPOSED, {"version": 1, **plan_payload}),
            (TaskEventType.PLAN_ACCEPTED, {"version": 1}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.ASSESSMENT_RECORDED,
                {
                    "attempt_id": "assess-1",
                    "matrix_results": [],
                    "gaps": [],
                    "verdict": "replan",
                },
            ),
            (
                TaskEventType.REPLAN_PROPOSED,
                {"version": 2, "reason": "assessment requested a re-plan"},
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.PLANNING.value}),
            (TaskEventType.PLAN_PROPOSED, {"version": 2, **plan_payload}),
            (TaskEventType.PLAN_ACCEPTED, {"version": 2}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.EXECUTING.value}),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.ASSESSING.value}),
            (
                TaskEventType.CYCLE_COMPLETED,
                {
                    "cycle": 1,
                    "outcome": "succeeded",
                    "next_run_at": (NOW - timedelta(minutes=1)).isoformat(),
                },
            ),
            (TaskEventType.TASK_STATUS_CHANGED, {"status": TaskStatus.SCHEDULED.value}),
        ],
    )
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.SCHEDULED
    assert record.plan_version == 2
    assert len(_comment_bodies(github, "Accepted plan v2")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_start_step_tracks_the_work_issue_once(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, _runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.COMPLETE
    assert len(_comment_bodies(github, "Step s1 started as w-s1-1")) == 1
    assert len(_comment_bodies(github, "Issue: https://github.com/o/r/issues/1")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_record_outcome_tracks_the_pull_request_once(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )
    coordinator._MAX_COMMANDS = 3
    runner.pull_request_urls["w-s1-1"] = "https://github.com/o/r/pull/7"

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await coordinator.drive(record, lease_epoch=epoch)
    del coordinator._MAX_COMMANDS
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.COMPLETE
    assert len(_comment_bodies(github, "Step s1 merged through w-s1-1")) == 1
    assert len(_comment_bodies(github, "Pull request: https://github.com/o/r/pull/7")) == 1
    assert len(github.comments) == before


@pytest.mark.asyncio
async def test_assessment_tracks_verdict_and_gaps_once(stores, tmp_path, monkeypatch) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
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
    github = RecordingGitHub()
    coordinator._channels = (
        GitHubIssueDecisionChannel(store=task_store, github_factory=lambda _s: github),
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="runner-1", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    before = len(github.comments)
    record = await coordinator.drive(record, lease_epoch=epoch)

    assert record.status is TaskStatus.BLOCKED
    assessment_comments = _comment_bodies(github, "Assessment cycle 1: replan")
    assert len(assessment_comments) == 1
    assert "deterministic check failed (suggested step: repair-step)" in assessment_comments[0]
    assert len(github.comments) == before
