# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The software ProfileRunner creates labelled step issues and drives them on one stack."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sagewai.work.models import ProposedAcceptanceCriterion, WorkRecord
from sagewai.work.profiles.software.github import BaseMovedError
from sagewai.work.store import WorkStore
from sagewai.work.tasks.models import ExecutionRoute
from sagewai.work.tasks.plan import MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.software import (
    SoftwareProfileRunner,
    step_marker,
    task_label,
)
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_software_kernel import RecordingGitHub
from tests.work.tasks.test_store import _task

STEP = PlanStep(
    id="s1",
    title="Add the retry queue",
    goal="Add the retry queue",
    allowed_scope=("src",),
    acceptance_criteria=(
        ProposedAcceptanceCriterion(
            statement="the suite passes", verification_kind="deterministic"
        ),
    ),
    risk="low",
    domain="backend",
)


def _runner(work_store: WorkStore, github, **kwargs) -> SoftwareProfileRunner:
    return SoftwareProfileRunner(
        work_store=work_store, github_factory=lambda _scope: github, **kwargs
    )


class _RecordingSink:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecordingResumeLifecycle:
    def __init__(self, store: WorkStore) -> None:
        self.store = store
        self.calls: list[tuple[str, str | None]] = []

    async def resume(self, work_id: str, *, project_id: str | None) -> WorkRecord:
        self.calls.append((work_id, project_id))
        record = await self.store.load_work(work_id, project_id=project_id)
        if record is None:
            raise KeyError(work_id)
        record.status = "WORK_BLOCKED"
        await self.store.save_work(record)
        return record


def _stack_object(
    work_store: WorkStore,
    *,
    lifecycle: object,
    sink: _RecordingSink | None = None,
):
    return SimpleNamespace(
        lifecycle=lifecycle,
        work_store=work_store,
        worktree_manager=object(),
        activity_sink=sink or _RecordingSink(),
        capsule_compiler=object(),
        read_controller=object(),
        read_capabilities=object(),
        analysis_runtime=object(),
    )


async def _save_work(
    work_store: WorkStore,
    *,
    work_id: str,
    project_id: str = "project-a",
    status: str = "IMPLEMENTING",
    source_ref: str | None = None,
    profile_context: dict | None = None,
) -> WorkRecord:
    now = datetime.now(timezone.utc)
    record = WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref=source_ref,
        profile="software",
        status=status,
        contract_version=1,
        active_run_id=None,
        pending_gate=None,
        profile_context=profile_context or {},
        created_at=now,
        updated_at=now,
    )
    await work_store.save_work(record)
    return record


@pytest.mark.asyncio
async def test_create_issue_carries_the_task_label_and_the_step_marker(
    dialect_engine,  # noqa: F811
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    runner = _runner(work_store, github)
    url = await runner.create_issue(task, cycle=1, step=STEP)
    assert url.endswith("/issues/1")
    created = github.created[0]
    assert created["labels"] == (task_label(task),)
    assert created["title"] == "Add the retry queue"
    assert step_marker(task, cycle=1, step=STEP) in created["body"]
    assert "the suite passes" in created["body"]


@pytest.mark.asyncio
async def test_find_issue_reads_back_the_marker_after_a_crashed_create(
    dialect_engine,  # noqa: F811
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    runner = _runner(work_store, github)
    assert await runner.find_issue(task, cycle=1, step=STEP) is None
    url = await runner.create_issue(task, cycle=1, step=STEP)
    assert await runner.find_issue(task, cycle=1, step=STEP) == url
    assert await runner.find_issue(task, cycle=2, step=STEP) is None
    other = STEP.model_copy(update={"id": "s2"})
    assert await runner.find_issue(task, cycle=1, step=other) is None


@pytest.mark.asyncio
async def test_the_marker_match_is_whole_line_so_s1_never_matches_s10(
    dialect_engine,  # noqa: F811
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    runner = _runner(work_store, github)
    wide = STEP.model_copy(update={"id": "s10"})
    url = await runner.create_issue(task, cycle=1, step=wide)
    assert await runner.find_issue(task, cycle=1, step=wide) == url
    assert await runner.find_issue(task, cycle=1, step=STEP) is None


@pytest.mark.asyncio
async def test_find_work_ignores_superseded_reruns_and_the_excluded_work(
    dialect_engine,  # noqa: F811
) -> None:
    from datetime import datetime, timezone

    from sagewai.work.models import SUPERSEDED, WorkRecord

    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _task(project_id="project-a")
    runner = _runner(work_store, RecordingGitHub())
    url = "https://github.com/o/r/issues/1"
    now = datetime.now(timezone.utc)
    for work_id, status in (("w-old", SUPERSEDED), ("w-new", "IMPLEMENTING")):
        await work_store.save_work(
            WorkRecord(
                work_id=work_id,
                project_id="project-a",
                source_ref=url,
                profile="software",
                status=status,
                contract_version=1,
                active_run_id=None,
                pending_gate=None,
                profile_context={"task_id": task.id, "base_sha": "a" * 40},
                created_at=now,
                updated_at=now,
            )
        )
    found = await runner.find_work(task, issue_url=url)
    assert found is not None and found.work_id == "w-new"
    assert await runner.find_work(task, issue_url=url, exclude="w-new") is None
    assert await runner.find_work(task, issue_url="https://github.com/o/r/issues/9") is None


@pytest.mark.asyncio
async def test_base_sha_fetches_origin_and_reads_the_default_branch_head(
    tmp_path,
    dialect_engine,  # noqa: F811
) -> None:
    from tests.work.test_lifecycle import _git, _repository

    origin_parent = tmp_path / "origin"
    origin_parent.mkdir()
    origin, head = _repository(origin_parent)
    _git(origin, "branch", "-M", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _task(project_id="project-a")
    task = task.model_copy(
        update={"target": task.target.model_copy(update={"repository_path": str(clone)})}
    )
    runner = _runner(work_store, RecordingGitHub())
    assert await runner.base_sha(task) == head


@pytest.mark.asyncio
async def test_start_retries_once_on_base_moved_error(
    tmp_path,
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    from tests.work.test_github import FakeBranchPublisher, FakeSoftwareLifecycle
    from tests.work.test_lifecycle import _git, _repository

    origin_parent = tmp_path / "origin"
    origin_parent.mkdir()
    origin, head = _repository(origin_parent)
    _git(origin, "branch", "-M", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    task = task.model_copy(
        update={"target": task.target.model_copy(update={"repository_path": str(clone)})}
    )
    fake_lifecycle = FakeSoftwareLifecycle(work_store, pause_analysis=True)
    stack = _stack_object(work_store, lifecycle=fake_lifecycle)

    async def fake_stack(**_kwargs):
        return stack

    publisher = FakeBranchPublisher()
    publisher.fail_phases.add("intake")
    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    monkeypatch.setattr(
        "sagewai.work.profiles.software.assembly.WorktreeBranchPublisher",
        lambda **_kwargs: publisher,
    )
    runner = _runner(work_store, github)
    issue_url = await runner.create_issue(task, cycle=1, step=STEP)

    record = await runner.start(task, cycle=1, step=STEP, issue_url=issue_url, base_sha="0" * 40)

    assert record.profile_context["base_sha"] == head
    assert [validation[2] for validation in publisher.validations] == ["0" * 40, head]


@pytest.mark.asyncio
async def test_start_propagates_a_second_base_moved_error(
    tmp_path,
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    from tests.work.test_github import FakeBranchPublisher, FakeSoftwareLifecycle
    from tests.work.test_lifecycle import _git, _repository

    origin_parent = tmp_path / "origin"
    origin_parent.mkdir()
    origin, head = _repository(origin_parent)
    _git(origin, "branch", "-M", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    task = task.model_copy(
        update={"target": task.target.model_copy(update={"repository_path": str(clone)})}
    )
    stack = _stack_object(
        work_store,
        lifecycle=FakeSoftwareLifecycle(work_store, pause_analysis=True),
    )

    async def fake_stack(**_kwargs):
        return stack

    publisher = FakeBranchPublisher()
    publisher.fail_phases.update({"intake", "publish"})
    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    monkeypatch.setattr(
        "sagewai.work.profiles.software.assembly.WorktreeBranchPublisher",
        lambda **_kwargs: publisher,
    )
    runner = _runner(work_store, github)
    issue_url = await runner.create_issue(task, cycle=1, step=STEP)

    with pytest.raises(BaseMovedError):
        await runner.start(task, cycle=1, step=STEP, issue_url=issue_url, base_sha="0" * 40)

    assert [validation[2] for validation in publisher.validations] == ["0" * 40, head]


@pytest.mark.asyncio
async def test_is_merged_returns_false_for_empty_context_and_reads_pull_request(
    dialect_engine,  # noqa: F811
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    runner = _runner(work_store, github)
    await _save_work(work_store, work_id="work-empty", profile_context={})
    assert await runner.is_merged(task, work_id="work-empty") is False
    assert github.pull_request_reads == []

    await _save_work(
        work_store,
        work_id="work-pr",
        profile_context={
            "github": {
                "project_id": "project-a",
                "owner": "octocat",
                "repo": "hello-world",
                "issue_number": 42,
                "issue_url": "https://github.com/octocat/hello-world/issues/42",
                "default_branch": "main",
                "branch": "sagewai/work-1",
                "branch_sha": "b" * 40,
                "pull_request_number": 7,
                "pull_request_url": "https://github.com/octocat/hello-world/pull/7",
                "merged_sha": None,
            }
        },
    )
    assert await runner.is_merged(task, work_id="work-pr") is False
    github.merged_sha = "c" * 40
    assert await runner.is_merged(task, work_id="work-pr") is True
    assert [item.number for item in github.pull_request_reads] == [7, 7]


@pytest.mark.asyncio
async def test_resume_passes_the_task_project_to_the_lifecycle(
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    await _save_work(work_store, work_id="work-1", project_id="project-a")
    fake_lifecycle = _RecordingResumeLifecycle(work_store)
    stack = _stack_object(work_store, lifecycle=fake_lifecycle)

    async def fake_stack(**_kwargs):
        return stack

    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    runner = _runner(work_store, github)

    await runner.resume(task, cycle=1, work_id="work-1")

    assert fake_lifecycle.calls == [("work-1", "project-a")]


@pytest.mark.asyncio
async def test_plan_wires_task_planner_from_the_stack(
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    stack = _stack_object(work_store, lifecycle=object())
    task = _task(project_id="project-a")
    planner_kwargs = []

    async def fake_stack(**_kwargs):
        return stack

    class RecordingPlanner:
        def __init__(self, **kwargs) -> None:
            planner_kwargs.append(kwargs)

        async def plan(self, task, **kwargs) -> TaskPlanResult:
            return TaskPlanResult(
                attempt_id="plan",
                steps=(STEP,),
                acceptance_matrix=(
                    MatrixItem(
                        id="m1",
                        statement="verification passes",
                        verification_kind="deterministic",
                        command="just smoke",
                    ),
                ),
            )

    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    monkeypatch.setattr("sagewai.work.tasks.software.TaskPlanner", RecordingPlanner)
    runner = _runner(work_store, RecordingGitHub())

    await runner.plan(
        task,
        cycle=1,
        plan_version=1,
        base_sha="a" * 40,
        brief_text="Build the thing",
        amendments=(),
    )

    assert planner_kwargs == [
        {
            "work_store": stack.work_store,
            "capsule_compiler": stack.capsule_compiler,
            "controller": stack.read_controller,
            "runtime": stack.analysis_runtime,
            "capabilities": stack.read_capabilities,
            "worktree_manager": stack.worktree_manager,
            "scratch_manager": planner_kwargs[0]["scratch_manager"],
        }
    ]


def test_target_rejects_a_task_whose_profile_is_not_software() -> None:
    task = _task().model_copy(update={"profile": "report"})
    with pytest.raises(ValueError):
        SoftwareProfileRunner._target(task)


@pytest.mark.asyncio
async def test_task_routing_and_attempt_budget_reach_the_software_stack_builder(
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    calls = []

    async def fake_stack(**kwargs):
        calls.append(kwargs)
        return _stack_object(work_store, lifecycle=object())

    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    task = _task(project_id="project-a")
    task = task.model_copy(
        update={
            "routing": task.routing.model_copy(update={"prefer_free_implementation": True}),
            "budget": task.budget.model_copy(update={"max_attempts_per_stage": 5}),
        }
    )
    runner = _runner(work_store, RecordingGitHub(), engine=dialect_engine)

    await runner._stack(task)

    assert calls[0]["prefer_free_implementation"] is True
    assert calls[0]["max_attempts_per_stage"] == 5


@pytest.mark.asyncio
async def test_stack_cache_is_bounded_and_keyed_by_stack_shape(
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    builds = []

    async def fake_stack(**kwargs):
        sink = _RecordingSink()
        stack = _stack_object(work_store, lifecycle=object(), sink=sink)
        builds.append((kwargs, sink))
        return stack

    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    runner = _runner(work_store, RecordingGitHub(), engine=dialect_engine)
    task = _task(task_id="task-cache", project_id="project-a")
    variants = (
        task,
        task.model_copy(
            update={
                "execution": ExecutionRoute(route="fleet", fleet_org_id="org-a"),
            }
        ),
        task.model_copy(
            update={
                "routing": task.routing.model_copy(update={"prefer_free_implementation": True}),
            }
        ),
        task.model_copy(
            update={"budget": task.budget.model_copy(update={"max_attempts_per_stage": 5})}
        ),
        task.model_copy(
            update={
                "target": task.target.model_copy(
                    update={"verification_image": "sha256:" + "c" * 64}
                )
            }
        ),
        task.model_copy(
            update={
                "target": task.target.model_copy(update={"verification_commands": ("just test",)})
            }
        ),
    )
    for variant in variants:
        await runner._stack(variant)
    assert len(builds) == len(variants)

    for index in range(5):
        await runner._stack(_task(task_id=f"task-cache-{index}", project_id="project-a"))

    assert len(runner._stacks) == 8
    assert [sink.closed for _, sink in builds[:3]] == [True, True, True]
    assert [sink.closed for _, sink in builds[3:]] == [False] * 8


@pytest.mark.asyncio
async def test_start_drives_one_step_on_a_stack_built_from_the_test_engine(
    tmp_path,
    dialect_engine,  # noqa: F811
    monkeypatch,
) -> None:
    """The one path that assembles the real stack; everything else fakes the runner."""
    from tests.work.test_github import FakeBranchPublisher, FakeSoftwareLifecycle
    from tests.work.test_lifecycle import _repository

    monkeypatch.setenv("SAGEWAI_WORK_VERIFICATION_IMAGE", "sha256:" + "b" * 64)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    repository_parent = tmp_path / "repo"
    repository_parent.mkdir()
    repository, head = _repository(repository_parent)
    from tests.work.test_lifecycle import _git

    _git(repository, "branch", "-M", "main")
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    github = RecordingGitHub()
    task = _task(project_id="project-a")
    task = task.model_copy(
        update={"target": task.target.model_copy(update={"repository_path": str(repository)})}
    )
    runner = _runner(work_store, github, engine=dialect_engine)
    stack = await runner._stack(task)
    assert stack.lifecycle.__class__.__name__ == "SoftwareLifecycle"
    assert stack is await runner._stack(task)
    stack_key = next(key for key, value in runner._stacks.items() if value is stack)
    runner._stacks[stack_key] = replace(stack, lifecycle=FakeSoftwareLifecycle(work_store))
    monkeypatch.setattr(
        "sagewai.work.profiles.software.assembly.WorktreeBranchPublisher",
        lambda **_kwargs: FakeBranchPublisher(),
    )
    issue_url = await runner.create_issue(task, cycle=1, step=STEP)
    record = await runner.start(task, cycle=1, step=STEP, issue_url=issue_url, base_sha=head)
    assert record.profile_context["task_id"] == task.id
    assert record.source_ref == issue_url
    await runner.aclose()
