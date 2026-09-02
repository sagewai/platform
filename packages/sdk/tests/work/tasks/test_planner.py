# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Planning stage: a planning Work driven through OperatorController."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.core.state import InMemoryStore
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work import (
    CapabilityGrant,
    CapabilitySet,
    OperatorController,
    OperatorResult,
    TaskCapsuleCompiler,
    WorkEventType,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.models import ProposedAcceptanceCriterion
from sagewai.work.profiles.software import (
    SoftwareReadOnlyResultValidator,
    SoftwareWorktreeManager,
)
from sagewai.work.tasks.models import (
    Authority,
    ExecutionRoute,
    ReportTarget,
    SoftwareTarget,
    Task,
    TaskKind,
    TaskOrigin,
)
from sagewai.work.tasks.plan import MatrixItem, PlanStep, TaskPlanResult
from sagewai.work.tasks.planner import PlanningFailedError, TaskPlanner
from sagewai.work.tasks.scratch import ScratchResultValidator, ScratchWorkspaceManager
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(("git", "-C", str(repository), *args), text=True).strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "-q", str(repository)), check=True)
    subprocess.run(("git", "-C", str(repository), "config", "user.email", "test@example.com"), check=True)
    subprocess.run(("git", "-C", str(repository), "config", "user.name", "Test"), check=True)
    (repository / "AGENTS.md").write_text("Run deterministic verification.\n")
    (repository / "source.txt").write_text("base\n")
    subprocess.run(("git", "-C", str(repository), "add", "."), check=True)
    subprocess.run(("git", "-C", str(repository), "commit", "-qm", "base"), check=True)
    return repository, _git(repository, "rev-parse", "HEAD")


def _plan_result(attempt_id: str) -> TaskPlanResult:
    return TaskPlanResult(
        attempt_id=attempt_id,
        steps=(
            PlanStep(
                id="engine",
                title="Engine",
                goal="Add the engine",
                allowed_scope=("app/",),
                acceptance_criteria=(
                    ProposedAcceptanceCriterion(statement="tests pass", verification_kind="deterministic"),
                ),
                risk="low",
                domain="backend",
                size="s",
            ),
        ),
        acceptance_matrix=(
            MatrixItem(
                id="m1",
                statement="verification passes",
                verification_kind="deterministic",
                command="just smoke",
            ),
        ),
    )


class PlanningRuntime:
    name = "planning-runtime"

    def __init__(self, *, attempt_id: str | None = None, fail: bool = False) -> None:
        self.attempt_id = attempt_id
        self.fail = fail
        self.requests = []
        self.capsules = []

    async def run(self, request, capsule, capabilities, workspace):
        self.requests.append(request)
        self.capsules.append(capsule)
        plan = _plan_result(self.attempt_id or request.run_id)
        return OperatorResult(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            status="failed" if self.fail else "passed",
            summary="planned",
            evidence_refs=("evidence://brief",),
            artifact_refs=(),
            changes=(),
            verification=(),
            risks=(),
            action_results=(),
            profile_context={} if self.fail else {"task_plan_result": plan.model_dump(mode="json")},
        )


def _read_capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem.read",
                kind="filesystem",
                scope={"roots": ["."]},
                permissions=("workspace.read",),
            ),
        ),
    )


def _task(
    tmp_path: Path,
    artifacts: LocalArtifactStore,
    *,
    report: bool = False,
    repository: Path | None = None,
) -> Task:
    brief = artifacts.put_bytes(
        b"# Brief\n\nBuild the engine.\n",
        project_id="project-a",
        media_type="text/markdown",
        created_by="test",
    )
    target = (
        ReportTarget(required_sections=("Summary",))
        if report
        else SoftwareTarget(
            repository_path=str(repository),
            owner="o",
            repo="r",
            verification_image="sha256:" + "a" * 64,
        )
    )
    return Task(
        id="task-1",
        project_id="project-a",
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Build the engine",
        brief_ref=brief,
        brief_summary="Build the engine",
        template_id="software_delivery",
        template_version="1",
        profile="report" if report else "software",
        target=target,
        authority=Authority.for_kind(TaskKind.BATCH),
        execution=ExecutionRoute(route="local"),
        created_by="arda",
        created_at=NOW,
    )


async def _planner(engine, tmp_path: Path, runtime, *, report: bool = False) -> tuple[TaskPlanner, WorkStore]:
    work_store = WorkStore(engine=engine)
    await work_store.init()
    knowledge_store = KnowledgeStore(engine=engine)
    await knowledge_store.init()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    validator = ScratchResultValidator() if report else SoftwareReadOnlyResultValidator()
    controller = OperatorController(
        work_store=work_store,
        durability_store=InMemoryStore(),
        permission_policy=PermissionPolicy(),
        control_checks={},
        result_validator=validator,
        heartbeat_interval=0.01,
    )
    planner = TaskPlanner(
        work_store=work_store,
        capsule_compiler=TaskCapsuleCompiler(knowledge_store=knowledge_store, artifact_store=artifacts),
        controller=controller,
        runtime=runtime,
        capabilities=_read_capabilities(),
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
        scratch_manager=ScratchWorkspaceManager(root=tmp_path / "scratch"),
    )
    return planner, work_store


@pytest.mark.asyncio
async def test_planner_runs_planning_work_in_pinned_worktree(dialect_engine, tmp_path: Path) -> None:  # noqa: F811
    repository, base_sha = _repository(tmp_path)
    runtime = PlanningRuntime()
    planner, work_store = await _planner(dialect_engine, tmp_path, runtime)
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    task = _task(tmp_path, artifacts, repository=repository)

    result = await planner.plan(task, cycle=1, plan_version=1, base_sha=base_sha, brief_text="# Brief\n\nBuild the engine.\n")

    assert result.attempt_id == "task-1:plan:1:1:plan:1"
    assert [step.id for step in result.steps] == ["engine"]
    request = runtime.requests[0]
    assert request.stage == "plan" and request.work_id == "task-1:plan:1:1"
    capsule = runtime.capsules[0]
    assert capsule.profile_context["task_plan_result_schema"]["title"] == "TaskPlanResult"
    assert capsule.profile_context["brief"].startswith("# Brief")
    assert capsule.profile_context["verification_commands"] == ["just smoke"]
    assert task.brief_ref.storage_ref in capsule.contract.evidence_refs
    events = await work_store.read_events("task-1:plan:1:1", project_id="project-a")
    kinds = [event.event_type for event in events]
    assert kinds[:2] == [WorkEventType.WORK_CREATED, WorkEventType.CONTRACT_PROPOSED]
    assert WorkEventType.STAGE_STARTED in kinds and WorkEventType.EXECUTION_RECORDED in kinds
    assert kinds[-1] is WorkEventType.WORK_COMPLETED
    record = await work_store.load_work("task-1:plan:1:1", project_id="project-a")
    assert record is not None and record.status == "COMPLETE"


@pytest.mark.asyncio
async def test_planner_is_idempotent_per_cycle_and_version(dialect_engine, tmp_path: Path) -> None:  # noqa: F811
    repository, base_sha = _repository(tmp_path)
    runtime = PlanningRuntime()
    planner, work_store = await _planner(dialect_engine, tmp_path, runtime)
    task = _task(tmp_path, LocalArtifactStore(root=tmp_path / "objects"), repository=repository)
    await planner.plan(task, cycle=1, plan_version=1, base_sha=base_sha, brief_text="b")
    await planner.plan(task, cycle=1, plan_version=1, base_sha=base_sha, brief_text="b")
    events = await work_store.read_events("task-1:plan:1:1", project_id="project-a")
    assert sum(1 for event in events if event.event_type is WorkEventType.WORK_CREATED) == 1
    assert len(runtime.requests) == 1  # durable run replayed, runtime not re-invoked


@pytest.mark.asyncio
async def test_planner_rejects_wrong_attempt_and_failed_stage(dialect_engine, tmp_path: Path) -> None:  # noqa: F811
    repository, base_sha = _repository(tmp_path)
    planner, _ = await _planner(dialect_engine, tmp_path, PlanningRuntime(attempt_id="other"))
    task = _task(tmp_path, LocalArtifactStore(root=tmp_path / "objects"), repository=repository)
    with pytest.raises(PlanningFailedError) as excinfo:
        await planner.plan(task, cycle=1, plan_version=1, base_sha=base_sha, brief_text="b")
    assert "attempt" in str(excinfo.value)
    planner, _ = await _planner(dialect_engine, tmp_path, PlanningRuntime(fail=True))
    with pytest.raises(PlanningFailedError):
        await planner.plan(task, cycle=2, plan_version=1, base_sha=base_sha, brief_text="b")


@pytest.mark.asyncio
async def test_planner_uses_scratch_workspace_for_report_targets(dialect_engine, tmp_path: Path) -> None:  # noqa: F811
    runtime = PlanningRuntime()
    planner, _ = await _planner(dialect_engine, tmp_path, runtime, report=True)
    task = _task(tmp_path, LocalArtifactStore(root=tmp_path / "objects"), report=True)
    result = await planner.plan(task, cycle=1, plan_version=1, base_sha=None, brief_text="b")
    assert result.steps
    assert runtime.capsules[0].profile_context["verification_commands"] == []
    assert runtime.requests[0].action_scope.allowed_targets == (".",)
    assert (tmp_path / "scratch" / "project-a" / "task-1:plan:1:1" / "plan").is_dir()


@pytest.mark.asyncio
async def test_failed_planning_blocks_the_planning_work(dialect_engine, tmp_path: Path) -> None:  # noqa: F811
    repository, base_sha = _repository(tmp_path)
    planner, work_store = await _planner(dialect_engine, tmp_path, PlanningRuntime(fail=True))
    task = _task(tmp_path, LocalArtifactStore(root=tmp_path / "objects"), repository=repository)
    with pytest.raises(PlanningFailedError):
        await planner.plan(task, cycle=1, plan_version=1, base_sha=base_sha, brief_text="b")
    record = await work_store.load_work("task-1:plan:1:1", project_id="project-a")
    assert record is not None and record.status == "WORK_BLOCKED"
    events = await work_store.read_events("task-1:plan:1:1", project_id="project-a")
    assert events[-1].event_type is WorkEventType.WORK_BLOCKED
    assert events[-1].payload_json["reason"] == "planning_failed"
