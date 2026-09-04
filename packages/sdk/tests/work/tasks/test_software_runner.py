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

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import sagewai.work.profiles.software.assembly as software_assembly
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.harness.discovery import DiscoveredServer
from sagewai.work.models import (
    CriterionVerification,
    ProposedAcceptanceCriterion,
    VerificationResult,
    WorkRecord,
)
from sagewai.work.profiles.software.assembly import (
    build_software_stack,
    resolve_credential_values,
)
from sagewai.work.profiles.software.github import BaseMovedError
from sagewai.work.profiles.software.models import SoftwareWorkspace
from sagewai.work.runtime import CapabilityGrant, CapabilitySet, OperatorResult
from sagewai.work.runtime_harness import HarnessRuntime
from sagewai.work.store import WorkStore
from sagewai.work.tasks.assessor import AssessmentFailedError, TaskAssessor
from sagewai.work.tasks.coordinator import TaskCoordinator
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import ExecutionRoute, HarnessTier, TaskDefaults, TaskOrigin
from sagewai.work.tasks.plan import AcceptedPlan, MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.runner import TaskCoordinatorRunner
from sagewai.work.tasks.service import TaskService
from sagewai.work.tasks.software import (
    SoftwareProfileRunner,
    step_marker,
    task_label,
)
from sagewai.work.tasks.store import TaskStore
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_software_kernel import RecordingGitHub
from tests.work.tasks.test_store import _task

PROJECT = "project-a"
IMAGE = "sha256:" + "b" * 64
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
BRIEF = (
    "Implement the retry queue in the payments service repository with a failing test first "
    "and open a pull request when the deterministic verification command passes."
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
    worktree_manager: object | None = None,
    capsule_compiler: object | None = None,
    read_controller: object | None = None,
    read_capabilities: object | None = None,
    analysis_runtime: object | None = None,
    verifier: object | None = None,
):
    return SimpleNamespace(
        lifecycle=lifecycle,
        work_store=work_store,
        worktree_manager=worktree_manager or object(),
        activity_sink=sink or _RecordingSink(),
        capsule_compiler=capsule_compiler or object(),
        read_controller=read_controller or object(),
        read_capabilities=read_capabilities or object(),
        analysis_runtime=analysis_runtime or object(),
        verifier=verifier or object(),
    )


class _RecordingVerifier:
    def __init__(self) -> None:
        self.calls = []

    async def verify(
        self,
        *,
        work_item,
        contract,
        criterion_ids,
        attempt_id,
        run_id,
        workspace,
        commands,
    ) -> VerificationResult:
        self.calls.append(
            {
                "work_id": work_item.id,
                "contract_id": contract.id,
                "criterion_ids": criterion_ids,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "workspace": workspace,
                "commands": commands,
            }
        )
        return VerificationResult(
            project_id=work_item.project_id,
            contract_id=contract.id,
            attempt_id=attempt_id,
            stage="verification",
            passed=True,
            criterion_results=tuple(
                CriterionVerification(
                    project_id=work_item.project_id,
                    contract_id=contract.id,
                    criterion_id=criterion_id,
                    passed=True,
                    evidence_refs=(f"verify://{criterion_id}",),
                )
                for criterion_id in criterion_ids
            ),
            evidence_refs=("verify://aggregate",),
        )


class _RecordingWorktreeManager:
    def __init__(self, root) -> None:
        self.root = root
        self.prepared = []
        self.released = []

    async def prepare(
        self,
        *,
        repository,
        project_id,
        work_id,
        attempt_id,
        base_sha,
    ) -> SoftwareWorkspace:
        self.prepared.append(
            {
                "repository": repository,
                "project_id": project_id,
                "work_id": work_id,
                "attempt_id": attempt_id,
                "base_sha": base_sha,
            }
        )
        path = self.root / work_id / attempt_id
        path.mkdir(parents=True)
        return SoftwareWorkspace(
            ref=f"software://{work_id}/{attempt_id}",
            project_id=project_id,
            work_id=work_id,
            attempt_id=attempt_id,
            repository=repository,
            path=path,
            base_sha=base_sha,
            initial_sha=base_sha,
        )

    async def release(self, workspace) -> None:
        self.released.append(workspace)


class _RecordingCompiler:
    async def compile(self, **kwargs):
        return SimpleNamespace(profile_context=kwargs["profile_context"])


class _FailingAssessorController:
    async def run(self, *, runtime, request, capsule, capabilities, workspace) -> OperatorResult:
        return OperatorResult(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            status="failed",
            summary="assessor stopped",
            evidence_refs=(),
            artifact_refs=(),
            changes=(),
            verification=(),
            risks=(),
            action_results=(),
        )


class _FakeSandbox:
    """Enough of SandboxBackend for HarnessRuntime's grant validation to pass."""

    name = "fake"

    async def run(self, **_kwargs):
        raise AssertionError("the stack builder must not execute anything")


class _FakeConnections:
    """The connection store mcp_connection_resolver closes over."""

    def __init__(self) -> None:
        self.calls = []

    async def list(self, project_id: str | None, *, protocol: str):
        self.calls.append((project_id, protocol))
        return ()


class _FakeCredentials:
    """The credentials router mcp_connection_resolver closes over; never called here."""

    async def resolve(self, *, project_id: str, credential_ref: str):
        raise AssertionError("the stack builder must not resolve a connection secret")


class _FakeSecrets:
    """A SecretProvider that answers env_for once, at stack build."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.calls: list[list[str]] = []

    async def env_for(self, *, project_id, run_id, agent_id, declared_scopes, **_kwargs):
        self.calls.append(list(declared_scopes))
        return dict(self._values)


async def _project_a() -> tuple[str, ...]:
    return ("project-a",)


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
async def test_concurrent_software_tasks_meter_into_their_own_ledgers(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store = TaskStore(engine=dialect_engine)
    work_store = WorkStore(engine=dialect_engine)
    await task_store.init()
    await work_store.init()
    await task_store.put_defaults(
        TaskDefaults(project_id="project-a", target=_task().target), expected_revision=0
    )
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    service = TaskService(store=task_store, artifact_store=artifacts)
    tasks = [
        (
            await service.create(
                BRIEF,
                project_id="project-a",
                origin=TaskOrigin.HUMAN,
                created_by="test",
                now=datetime.now(timezone.utc),
            )
        )[0]
        for _ in range(2)
    ]
    gate = asyncio.Event()
    waiting = 0
    bound_ledgers: list[tuple[str, str]] = []
    reservations = []
    original_reserve = task_store.reserve_spend

    async def capture_reservation(reservation):
        reservations.append(reservation)
        await original_reserve(reservation)

    class ReservingPlanner:
        def __init__(self, **kwargs) -> None:
            self.controller = kwargs["controller"]

        async def plan(self, task, **_kwargs) -> TaskPlanResult:
            nonlocal waiting
            waiting += 1
            if waiting == 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            ledger = self.controller._ledger()
            await ledger.reserve(
                run_id=f"plan-{task.id}",
                stage="analysis",
                runtime=SimpleNamespace(name="claude"),
            )
            bound_ledgers.append((task.id, ledger.task_id))
            return TaskPlanResult(
                attempt_id=f"plan-{task.id}",
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

    async def fake_stack(**kwargs):
        controller = kwargs["controller_factory"](
            work_store=work_store,
            durability_store=object(),
            permission_policy=object(),
            control_checks={},
            result_validator=object(),
        )
        stack = _stack_object(work_store, lifecycle=object())
        stack.read_controller = controller
        return stack

    async def fake_base_sha(_task):
        return "a" * 40

    monkeypatch.setattr(task_store, "reserve_spend", capture_reservation)
    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    monkeypatch.setattr("sagewai.work.tasks.software.TaskPlanner", ReservingPlanner)
    profile = _runner(work_store, RecordingGitHub(), engine=dialect_engine)
    monkeypatch.setattr(profile, "base_sha", fake_base_sha)
    coordinator = TaskCoordinator(
        task_store=task_store,
        work_store=work_store,
        profile_runners=lambda _task: profile,
        artifact_store=artifacts,
    )
    runner = TaskCoordinatorRunner(
        task_store=task_store,
        driver=coordinator,
        list_project_ids=_project_a,
        max_tasks=2,
    )

    assert await runner.tick() == 2

    assert sorted(bound_ledgers) == sorted((task.id, task.id) for task in tasks)
    assert sorted((item.project_id, item.task_id, item.cycle) for item in reservations) == [
        ("project-a", task.id, 1) for task in sorted(tasks, key=lambda item: item.id)
    ]
    for task in tasks:
        totals = await task_store.spend_totals(task_id=task.id, project_id="project-a", cycle=1)
        assert totals.reservations == 1
        assert totals.usd_reserved == Decimal("5.00")
        events = await task_store.read_events(task.id, project_id="project-a")
        spend = [event for event in events if event.event_type is TaskEventType.SPEND_RESERVED]
        assert [event.payload_json["reservation_id"] for event in spend] == [f"plan-{task.id}"]


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


@pytest.mark.asyncio
async def test_assess_verifies_deterministic_items_at_the_supplied_merged_head_and_releases(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    verifier = _RecordingVerifier()
    worktrees = _RecordingWorktreeManager(tmp_path / "worktrees")
    controller = _FailingAssessorController()
    stack = _stack_object(
        work_store,
        lifecycle=object(),
        worktree_manager=worktrees,
        capsule_compiler=_RecordingCompiler(),
        read_controller=controller,
        read_capabilities=CapabilitySet(project_id="project-a", grants=()),
        analysis_runtime=SimpleNamespace(name="claude"),
        verifier=verifier,
    )
    task = _task(project_id="project-a")
    brief = LocalArtifactStore(root=tmp_path / "objects").put_bytes(
        b"# Brief\n\nBuild the thing.\n",
        project_id=task.project_id,
        media_type="text/markdown",
        created_by="test",
    )
    repository = tmp_path / "repo"
    task = task.model_copy(
        update={
            "brief_ref": brief,
            "target": task.target.model_copy(update={"repository_path": str(repository)}),
        }
    )
    plan = AcceptedPlan(
        version=1,
        steps=(STEP,),
        acceptance_matrix=(
            MatrixItem(
                id="smoke",
                statement="smoke passes",
                verification_kind="deterministic",
                command="just smoke",
            ),
            MatrixItem(
                id="lint",
                statement="lint passes",
                verification_kind="deterministic",
                command="just lint",
            ),
            MatrixItem(
                id="readback",
                statement="readback is correct",
                verification_kind="assessment",
            ),
        ),
    )

    async def fake_stack(**_kwargs):
        return stack

    async def fail_base_sha(_task):
        raise AssertionError("base_sha should not run when merged_sha is known")

    monkeypatch.setattr("sagewai.work.tasks.software.build_software_stack", fake_stack)
    runner = _runner(work_store, RecordingGitHub(), engine=dialect_engine)
    monkeypatch.setattr(runner, "base_sha", fail_base_sha)
    work_id = TaskAssessor.work_id(task, cycle=1, plan_version=2)

    with pytest.raises(AssessmentFailedError, match="assessor stopped"):
        await runner.assess(
            task,
            cycle=1,
            plan_version=2,
            plan=plan,
            outcomes={"s1": "accepted"},
            merged_sha="c" * 40,
            evidence=("git://" + "c" * 40,),
        )

    assert worktrees.prepared == [
        {
            "repository": repository,
            "project_id": "project-a",
            "work_id": work_id,
            "attempt_id": "assess-2",
            "base_sha": "c" * 40,
        }
    ]
    assert [workspace.base_sha for workspace in worktrees.released] == ["c" * 40]
    assert verifier.calls[0]["criterion_ids"] == (
        TaskAssessor.matrix_criterion_id(work_id, "smoke"),
        TaskAssessor.matrix_criterion_id(work_id, "lint"),
    )
    assert verifier.calls[0]["commands"] == ("just smoke", "just lint")
    assert verifier.calls[0]["run_id"] == f"{work_id}:verify:1"


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
async def test_software_runner_threads_harness_backends_to_the_stack_builder(
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
    sandbox = _FakeSandbox()
    connection_store = _FakeConnections()
    credentials = _FakeCredentials()
    secret_provider = _FakeSecrets({"GITHUB_TOKEN": "ghp_x"})
    runner = _runner(
        work_store,
        RecordingGitHub(),
        engine=dialect_engine,
        sandbox=sandbox,
        connection_store=connection_store,
        credentials=credentials,
        secret_provider=secret_provider,
    )

    await runner._stack(_task(project_id=PROJECT))

    assert calls[0]["sandbox"] is sandbox
    assert calls[0]["connection_store"] is connection_store
    assert calls[0]["credentials"] is credentials
    assert calls[0]["secret_provider"] is secret_provider


@pytest.mark.asyncio
async def test_the_harness_runtime_gets_its_sandbox_resolver_and_secrets(
    dialect_engine,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from tests.work.test_lifecycle import _repository

    repo, _base = _repository(tmp_path)
    store = TaskStore(engine=dialect_engine)
    await store.init()
    await store.put_defaults(
        TaskDefaults(
            project_id=PROJECT,
            target=_task(project_id=PROJECT).target,
            harness_tiers={"complex": HarnessTier(backend="localai", model="qwen")},
        ),
        expected_revision=0,
    )

    async def _discover_local_backends():
        return {
            "localai": DiscoveredServer(
                name="localai",
                base_url="http://127.0.0.1:8080",
                openai_compat_url="http://127.0.0.1:8080/v1",
                models=["qwen"],
            )
        }

    monkeypatch.setattr(software_assembly, "discover_local_backends", _discover_local_backends)
    connection_store = _FakeConnections()
    stack = await build_software_stack(
        project_id=PROJECT,
        repository=repo,
        verification_image=IMAGE,
        prefer_free_implementation=True,
        sandbox=_FakeSandbox(),
        connection_store=connection_store,
        credentials=_FakeCredentials(),
        credential_values={"GITHUB_TOKEN": "ghp_x"},
        engine=dialect_engine,
    )

    harness = stack.lifecycle._implementer.for_position(1).runtime
    assert harness._sandbox is not None
    assert harness._mcp_connections is not None
    with pytest.raises(KeyError):
        await harness._mcp_connections("github")
    assert connection_store.calls == [(PROJECT, "mcp")]
    assert harness._credential_values == {"GITHUB_TOKEN": "ghp_x"}


@pytest.mark.asyncio
async def test_credential_values_resolve_once_from_declared_refs() -> None:
    secrets = _FakeSecrets({"GITHUB_TOKEN": "ghp_x"})
    values = await resolve_credential_values(
        project_id=PROJECT,
        grants=(
            CapabilityGrant(
                project_id=PROJECT,
                name="github",
                kind="api",
                scope={},
                permissions=("request",),
                credential_ref="GITHUB_TOKEN",
            ),
            CapabilityGrant(
                project_id=PROJECT,
                name="github-again",
                kind="api",
                scope={},
                permissions=("request",),
                credential_ref="GITHUB_TOKEN",
            ),
        ),
        secret_provider=secrets,
        credential_values={"STATIC_TOKEN": "static"},
    )

    assert secrets.calls == [["GITHUB_TOKEN"]]
    assert values == {"STATIC_TOKEN": "static", "GITHUB_TOKEN": "ghp_x"}


@pytest.mark.asyncio
async def test_credential_values_skip_provider_without_declared_refs() -> None:
    secrets = _FakeSecrets({"GITHUB_TOKEN": "ghp_x"})
    values = await resolve_credential_values(
        project_id=PROJECT,
        grants=(
            CapabilityGrant(
                project_id=PROJECT,
                name="github",
                kind="api",
                scope={},
                permissions=("request",),
            ),
        ),
        secret_provider=secrets,
        credential_values={"STATIC_TOKEN": "static"},
    )

    assert secrets.calls == []
    assert values == {"STATIC_TOKEN": "static"}


@pytest.mark.asyncio
async def test_a_cli_grant_without_a_sandbox_still_fails_loudly() -> None:
    runtime = HarnessRuntime(
        tier="medium",
        tiers={"medium": HarnessTier(backend="localai", model="qwen")},
        backends={"localai": "http://127.0.0.1:8080/v1"},
    )
    grants = CapabilitySet(
        project_id=PROJECT,
        grants=(
            CapabilityGrant(
                project_id=PROJECT,
                name="cli.just",
                kind="cli",
                scope={"executable": "just"},
                permissions=("command.run",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="cli grants require a sandbox backend"):
        runtime._validate_grants_for_runtime(grants)


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


@pytest.mark.asyncio
async def test_eviction_keeps_a_ledger_while_another_key_of_the_task_remains(
    dialect_engine,  # noqa: F811
) -> None:
    from sagewai.work.tasks.budget import BudgetLedger
    from sagewai.work.tasks.store import TaskStore

    class _Sink:
        def __init__(self) -> None:
            self.closed = 0

        async def close(self) -> None:
            self.closed += 1

    class _Stack:
        def __init__(self) -> None:
            self.activity_sink = _Sink()

    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    runner = SoftwareProfileRunner(work_store=work_store, github_factory=lambda scope: RecordingGitHub())
    task = _task()
    ledger = BudgetLedger(
        store=TaskStore(engine=dialect_engine),
        task_id=task.id,
        project_id=task.project_id,
        cycle=1,
        budget=task.budget,
    )
    runner.use_ledger(ledger)
    local = task.model_copy(update={"execution": task.execution.model_copy(update={"route": "local"})})
    fleet = task.model_copy(
        update={
            "execution": task.execution.model_copy(
                update={"route": "fleet", "fleet_org_id": "org-1"}
            )
        }
    )
    first, second = _Stack(), _Stack()
    runner._stacks[runner._stack_key(local)] = first  # type: ignore[assignment]
    runner._stacks[runner._stack_key(fleet)] = second  # type: ignore[assignment]

    await runner._evict_oldest()
    assert first.activity_sink.closed == 1
    assert runner._ledgers[task.id] is ledger

    await runner._evict_oldest()
    assert second.activity_sink.closed == 1
    assert task.id not in runner._ledgers
