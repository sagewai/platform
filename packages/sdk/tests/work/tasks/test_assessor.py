# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assessment stage: a read-only internal Work judges one Task cycle."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.work import (
    CapabilityGrant,
    CapabilitySet,
    OperatorResult,
    TaskCapsuleCompiler,
    WorkStore,
)
from sagewai.work.events import WorkEventType
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.models import TASK_ASSESS_PROFILE, ProposedAcceptanceCriterion
from sagewai.work.tasks.assessment import MatrixResult
from sagewai.work.tasks.assessor import AssessmentFailedError, TaskAssessor
from sagewai.work.tasks.models import (
    Authority,
    ExecutionRoute,
    ReportTarget,
    SoftwareTarget,
    Task,
    TaskKind,
    TaskOrigin,
)
from sagewai.work.tasks.plan import AcceptedPlan, MatrixItem, PlanStep
from sagewai.work.tasks.scratch import ScratchWorkspaceManager
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
PROJECT = "project-a"


class _FakeController:
    """Return a canned OperatorResult, recording what the assessor asked for."""

    def __init__(self, payload: dict | None, status: str = "passed") -> None:
        self._payload = payload
        self._status = status
        self.requests: list = []
        self.capsules: list = []

    async def run(self, *, runtime, request, capsule, capabilities, workspace):
        self.requests.append(request)
        self.capsules.append(capsule)
        return OperatorResult(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            status=self._status,
            summary="assessed",
            evidence_refs=(),
            artifact_refs=(),
            changes=(),
            verification=(),
            risks=(),
            action_results=(),
            profile_context={}
            if self._payload is None
            else {"task_assessment_result": self._payload},
        )


class _StubRuntime:
    name = "claude"


def _read_capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id=PROJECT,
        grants=(
            CapabilityGrant(
                project_id=PROJECT,
                name="filesystem.read",
                kind="filesystem",
                scope={"roots": ["."]},
                permissions=("workspace.read",),
            ),
        ),
    )


async def _compiler(engine, tmp_path) -> TaskCapsuleCompiler:
    knowledge_store = KnowledgeStore(engine=engine)
    await knowledge_store.init()
    return TaskCapsuleCompiler(
        knowledge_store=knowledge_store,
        artifact_store=LocalArtifactStore(root=tmp_path / "objects"),
    )


def _report_task(tmp_path: Path) -> Task:
    brief = LocalArtifactStore(root=tmp_path / "objects").put_bytes(
        b"# Brief\n\nWrite the weekly report.\n",
        project_id=PROJECT,
        media_type="text/markdown",
        created_by="test",
    )
    return Task(
        id="task-1",
        project_id=PROJECT,
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Write the weekly report",
        brief_ref=brief,
        brief_summary="Write the weekly report",
        template_id="scheduled_research_report",
        template_version="2",
        profile="report",
        target=ReportTarget(required_sections=("Summary",)),
        authority=Authority.for_kind(TaskKind.BATCH),
        execution=ExecutionRoute(route="local"),
        created_by="arda",
        created_at=NOW,
    )


def _software_task(tmp_path: Path) -> Task:
    brief = LocalArtifactStore(root=tmp_path / "objects").put_bytes(
        b"# Brief\n\nUpdate the software project.\n",
        project_id=PROJECT,
        media_type="text/markdown",
        created_by="test",
    )
    return Task(
        id="task-1",
        project_id=PROJECT,
        kind=TaskKind.BATCH,
        origin=TaskOrigin.HUMAN,
        title="Update the software project",
        brief_ref=brief,
        brief_summary="Update the software project",
        template_id="software_delivery",
        template_version="1",
        profile="software",
        target=SoftwareTarget(
            repository_path=str(tmp_path / "repo"),
            owner="octocat",
            repo="hello-world",
            verification_image="sha256:" + "b" * 64,
        ),
        authority=Authority.for_kind(TaskKind.BATCH),
        execution=ExecutionRoute(route="local"),
        created_by="arda",
        created_at=NOW,
    )


def _plan_with(
    *,
    deterministic: tuple[str, ...],
    assessment: tuple[str, ...],
) -> AcceptedPlan:
    criterion = ProposedAcceptanceCriterion(
        statement="the report exists",
        verification_kind="policy",
    )
    return AcceptedPlan(
        version=1,
        steps=(
            PlanStep(
                id="s1",
                title="Draft",
                goal="Draft the report",
                allowed_scope=(".",),
                acceptance_criteria=(criterion,),
                risk="low",
                domain="report",
            ),
        ),
        acceptance_matrix=(
            *(
                MatrixItem(
                    id=item_id,
                    statement=f"{item_id} passes",
                    verification_kind="deterministic",
                    command="just smoke",
                )
                for item_id in deterministic
            ),
            *(
                MatrixItem(
                    id=item_id,
                    statement=f"{item_id} passes",
                    verification_kind="assessment",
                )
                for item_id in assessment
            ),
        ),
    )


async def _scratch(tmp_path, task: Task, *, cycle: int, plan_version: int):
    return await ScratchWorkspaceManager(root=tmp_path).prepare(
        project_id=task.project_id,
        work_id=TaskAssessor.work_id(task, cycle=cycle, plan_version=plan_version),
        attempt_id=f"assess-{plan_version}",
    )


@pytest.mark.asyncio
async def test_the_assessor_merges_the_verifier_and_its_own_verdict(dialect_engine, tmp_path):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _software_task(tmp_path)
    plan = _plan_with(deterministic=("smoke",), assessment=("grounded",))
    run_id = f"{TaskAssessor.work_id(task, cycle=1, plan_version=1)}:assess:1"
    controller = _FakeController(
        {
            "attempt_id": run_id,
            "matrix_results": [{"item_id": "grounded", "passed": True, "evidence_refs": []}],
            "gaps": [],
            "verdict": "accept",
        }
    )
    assessor = TaskAssessor(
        work_store=work_store,
        capsule_compiler=await _compiler(dialect_engine, tmp_path),
        controller=controller,
        runtime=_StubRuntime(),
        capabilities=_read_capabilities(),
    )

    async def deterministic(work_item, contract, workspace, run):
        return (MatrixResult(item_id="smoke", passed=True, evidence_refs=("git://abc",)),)

    result = await assessor.assess(
        task,
        cycle=1,
        plan_version=1,
        plan=plan,
        outcomes={"s1": "accepted"},
        workspace=await ScratchWorkspaceManager(root=tmp_path).prepare(
            project_id=task.project_id,
            work_id=TaskAssessor.work_id(task, cycle=1, plan_version=1),
            attempt_id="assess-1",
        ),
        evidence=("git://abc",),
        profile_context={"task_id": task.id, "cycle": 1},
        deterministic=deterministic,
    )

    assert result.verdict == "accept"
    assert {item.item_id for item in result.matrix_results} == {"smoke", "grounded"}
    assert controller.requests[0].stage == "assess"
    context = controller.capsules[0].profile_context
    assert "task_assessment_result_schema" in context
    assert context["deterministic_results"] == [
        {"item_id": "smoke", "passed": True, "evidence_refs": ["git://abc"]}
    ]
    assert context["step_outcomes"] == {"s1": "accepted"}
    assert context["evidence_refs"] == ["git://abc"]
    record = await work_store.load_work(
        TaskAssessor.work_id(task, cycle=1, plan_version=1), project_id=task.project_id
    )
    assert (record.profile, record.status) == (TASK_ASSESS_PROFILE, "COMPLETE")


@pytest.mark.asyncio
async def test_a_failing_deterministic_item_forces_a_replan(dialect_engine, tmp_path):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _software_task(tmp_path)
    plan = _plan_with(deterministic=("smoke",), assessment=("grounded",))
    run_id = f"{TaskAssessor.work_id(task, cycle=1, plan_version=1)}:assess:1"
    assessor = TaskAssessor(
        work_store=work_store,
        capsule_compiler=await _compiler(dialect_engine, tmp_path),
        controller=_FakeController(
            {
                "attempt_id": run_id,
                "matrix_results": [{"item_id": "grounded", "passed": True, "evidence_refs": []}],
                "gaps": [],
                "verdict": "accept",
            }
        ),
        runtime=_StubRuntime(),
        capabilities=_read_capabilities(),
    )

    async def deterministic(work_item, contract, workspace, run):
        return (MatrixResult(item_id="smoke", passed=False, evidence_refs=("git://abc",)),)

    result = await assessor.assess(
        task,
        cycle=1,
        plan_version=1,
        plan=plan,
        outcomes={"s1": "accepted"},
        workspace=await _scratch(tmp_path, task, cycle=1, plan_version=1),
        evidence=("git://abc",),
        profile_context={"task_id": task.id, "cycle": 1},
        deterministic=deterministic,
    )

    assert result.verdict == "replan"
    assert {item.item_id: item.passed for item in result.matrix_results} == {
        "smoke": False,
        "grounded": True,
    }


@pytest.mark.asyncio
async def test_deterministic_failure_blocks_the_assessor_work(dialect_engine, tmp_path):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _software_task(tmp_path)
    assessor = TaskAssessor(
        work_store=work_store,
        capsule_compiler=await _compiler(dialect_engine, tmp_path),
        controller=_FakeController(
            {
                "attempt_id": f"{TaskAssessor.work_id(task, cycle=1, plan_version=1)}:assess:1",
                "matrix_results": [],
                "gaps": [],
                "verdict": "accept",
            }
        ),
        runtime=_StubRuntime(),
        capabilities=_read_capabilities(),
    )

    async def deterministic(work_item, contract, workspace, run):
        raise ValueError("sandbox unavailable")

    with pytest.raises(AssessmentFailedError, match="sandbox unavailable"):
        await assessor.assess(
            task,
            cycle=1,
            plan_version=1,
            plan=_plan_with(deterministic=("smoke",), assessment=()),
            outcomes={"s1": "accepted"},
            workspace=await _scratch(tmp_path, task, cycle=1, plan_version=1),
            evidence=("git://abc",),
            profile_context={"task_id": task.id, "cycle": 1},
            deterministic=deterministic,
        )

    work_id = TaskAssessor.work_id(task, cycle=1, plan_version=1)
    record = await work_store.load_work(work_id, project_id=task.project_id)
    events = await work_store.read_events(work_id, project_id=task.project_id)
    blocked = next(event for event in events if event.event_type is WorkEventType.WORK_BLOCKED)
    assert record.status == "WORK_BLOCKED"
    assert blocked.payload_json == {
        "reason": "assessment_verification_failed",
        "decision_request": "assessment verification failed: ValueError: sandbox unavailable",
    }


@pytest.mark.asyncio
async def test_the_full_matrix_reaches_the_assessor_capsule(dialect_engine, tmp_path):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _report_task(tmp_path)
    matrix_ids = tuple(f"m{index}" for index in range(201))
    plan = _plan_with(deterministic=(), assessment=matrix_ids)
    run_id = f"{TaskAssessor.work_id(task, cycle=1, plan_version=1)}:assess:1"
    controller = _FakeController(
        {
            "attempt_id": run_id,
            "matrix_results": [
                {"item_id": item_id, "passed": True, "evidence_refs": []}
                for item_id in matrix_ids
            ],
            "gaps": [],
            "verdict": "accept",
        }
    )
    assessor = TaskAssessor(
        work_store=work_store,
        capsule_compiler=await _compiler(dialect_engine, tmp_path),
        controller=controller,
        runtime=_StubRuntime(),
        capabilities=_read_capabilities(),
    )

    result = await assessor.assess(
        task,
        cycle=1,
        plan_version=1,
        plan=plan,
        outcomes={"s1": "accepted"},
        workspace=await _scratch(tmp_path, task, cycle=1, plan_version=1),
        evidence=(),
        profile_context={"task_id": task.id, "cycle": 1},
    )

    assert result.verdict == "accept"
    assert len(controller.capsules[0].profile_context["acceptance_matrix"]) == 201


@pytest.mark.asyncio
async def test_a_missing_result_blocks_the_assessor_work(dialect_engine, tmp_path):  # noqa: F811
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _report_task(tmp_path)
    assessor = TaskAssessor(
        work_store=work_store,
        capsule_compiler=await _compiler(dialect_engine, tmp_path),
        controller=_FakeController(None),
        runtime=_StubRuntime(),
        capabilities=_read_capabilities(),
    )

    with pytest.raises(AssessmentFailedError, match="no task_assessment_result"):
        await assessor.assess(
            task,
            cycle=1,
            plan_version=1,
            plan=_plan_with(deterministic=(), assessment=("grounded",)),
            outcomes={"s1": "accepted"},
            workspace=await _scratch(tmp_path, task, cycle=1, plan_version=1),
            evidence=(),
            profile_context={"task_id": task.id, "cycle": 1},
        )

    work_id = TaskAssessor.work_id(task, cycle=1, plan_version=1)
    record = await work_store.load_work(work_id, project_id=task.project_id)
    assert record.status == "WORK_BLOCKED"
    assert await work_store.pending_attention(project_id=task.project_id) == ()


@pytest.mark.asyncio
async def test_a_replan_assesses_the_same_cycle_as_a_new_work(dialect_engine, tmp_path):  # noqa: F811
    """Section 11: a replan re-assesses cycle 1 at a head that has moved (R8)."""
    work_store = WorkStore(engine=dialect_engine)
    await work_store.init()
    task = _report_task(tmp_path)
    plan = _plan_with(deterministic=(), assessment=("grounded",))
    assessed = []
    for plan_version in (1, 2):
        run_id = f"{TaskAssessor.work_id(task, cycle=1, plan_version=plan_version)}:assess:1"
        assessor = TaskAssessor(
            work_store=work_store,
            capsule_compiler=await _compiler(dialect_engine, tmp_path),
            controller=_FakeController(
                {
                    "attempt_id": run_id,
                    "matrix_results": [
                        {"item_id": "grounded", "passed": True, "evidence_refs": []}
                    ],
                    "gaps": [],
                    "verdict": "accept",
                }
            ),
            runtime=_StubRuntime(),
            capabilities=_read_capabilities(),
        )
        assessed.append(
            await assessor.assess(
                task,
                cycle=1,
                plan_version=plan_version,
                plan=plan,
                outcomes={"s1": "accepted"},
                workspace=await _scratch(tmp_path, task, cycle=1, plan_version=plan_version),
                evidence=(),
                profile_context={"task_id": task.id, "cycle": 1},
            )
        )

    assert [result.verdict for result in assessed] == ["accept", "accept"]
    assert assessed[0].attempt_id != assessed[1].attempt_id
    for plan_version in (1, 2):
        record = await work_store.load_work(
            TaskAssessor.work_id(task, cycle=1, plan_version=plan_version),
            project_id=task.project_id,
        )
        assert record.status == "COMPLETE"
