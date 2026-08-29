# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pre-run policy and deterministic control-precondition enforcement."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from sagewai.core.state import InMemoryStore, StepStatus
from sagewai.safety.permissions import PermissionPolicy
from sagewai.work import (
    ActionIntent,
    ActionScope,
    CapabilityGrant,
    CapabilitySet,
    ControlPrecondition,
    ControlPreconditionKind,
    OperatorDisciplineReport,
    OperatorResult,
    Reversibility,
    TaskCapsule,
    WorkContract,
    WorkEvent,
    WorkEventType,
    WorkItem,
    WorkRequest,
    WorkStore,
)
from sagewai.work.control import (
    ControlCheckContext,
    ControlCheckResult,
    OperatorController,
    active_control_precondition_ids,
)
from tests.db.conftest import dialect_engine  # noqa: F401

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def test_control_event_fold_restores_only_the_named_preconditions() -> None:
    def event(sequence: int, event_type: WorkEventType, payload: dict) -> WorkEvent:
        return WorkEvent(
            id=f"event-{sequence}",
            project_id="project-a",
            work_id="work-1",
            sequence=sequence,
            event_type=event_type,
            actor_type="test",
            actor_ref=None,
            payload_json=payload,
            created_at=NOW,
        )

    events = [
        event(
            1,
            WorkEventType.CONTROL_DEGRADED,
            {"failed_preconditions": ["authority"]},
        ),
        event(
            2,
            WorkEventType.CONTROL_DEGRADED,
            {"failed_preconditions": ["observability"]},
        ),
        event(
            3,
            WorkEventType.CONTROL_RESTORED,
            {"precondition_ids": ["observability"]},
        ),
    ]

    assert active_control_precondition_ids(events) == {"authority"}


def _intent() -> ActionIntent:
    return ActionIntent(
        project_id="project-a",
        action_id="action-1",
        capability="filesystem.write",
        target="packages/sdk/sagewai/work",
        expected_effect="Scoped files change",
        scope={"allowed_targets": ["packages/sdk/sagewai/work"]},
        risk="low",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        required_permission="workspace.write",
        evidence_refs=("contract://1",),
    )


def _precondition(kind: ControlPreconditionKind, check_ref: str) -> ControlPrecondition:
    return ControlPrecondition(
        id=f"precondition-{kind.value}",
        project_id="project-a",
        kind=kind,
        description=f"{kind.value} remains available",
        check_ref=check_ref,
        required_for=("implement",),
    )


def _request(
    *,
    run_id: str = "run-1",
    preconditions: tuple[ControlPrecondition, ...] = (),
) -> WorkRequest:
    return WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id=run_id,
        stage="implement",
        action_scope=ActionScope(
            project_id="project-a",
            objective="Implement runtime",
            allowed_targets=("packages/sdk/sagewai/work",),
            allowed_capabilities=("filesystem.write",),
        ),
        action_intents=(_intent(),),
        control_preconditions=preconditions,
    )


def _capsule() -> TaskCapsule:
    item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref=None,
        title="Runtime",
        description="Implement runtime",
        created_at=NOW,
    )
    contract = WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Implement runtime",
        allowed_scope=("packages/sdk/sagewai/work",),
        acceptance_criteria=("control checks pass",),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
    )
    return TaskCapsule(
        project_id="project-a",
        work_id="work-1",
        stage="implement",
        work_item=item,
        contract=contract,
        knowledge_refs=(),
        knowledge_items=(),
        knowledge_items_considered=4,
        artifact_bytes_referenced=123,
        open_assumption_ids=(),
        prior_result_refs=(),
    )


def _capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem.write",
                kind="filesystem",
                scope={"roots": ["packages/sdk/sagewai/work"]},
                permissions=("workspace.write",),
            ),
            CapabilityGrant(
                project_id="project-a",
                name="production.deploy",
                kind="api",
                scope={"environment": "production"},
                permissions=("deploy",),
                credential_ref="credential://production",
            ),
        ),
    )


def _result(run_id: str) -> OperatorResult:
    return OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id=run_id,
        status="passed",
        summary="done",
        evidence_refs=("command://fake",),
        artifact_refs=(),
        changes=(),
        verification=("fake",),
        risks=(),
        action_results=(),
        profile_context={},
    )


class RecordingRuntime:
    name = "fake"

    def __init__(self, *, block: bool = False) -> None:
        self.started = 0
        self.capabilities: CapabilitySet | None = None
        self.started_event = asyncio.Event()
        self.cancelled = False
        self._block = block

    async def run(self, request, capsule, capabilities, workspace):
        self.started += 1
        self.capabilities = capabilities
        self.started_event.set()
        if self._block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return _result(request.run_id)


class SequenceControlCheck:
    def __init__(self, *passed: bool) -> None:
        self._passed = list(passed)
        self.calls = 0

    async def evaluate(self, context: ControlCheckContext) -> ControlCheckResult:
        value = self._passed.pop(0) if self._passed else True
        self.calls += 1
        return ControlCheckResult(
            project_id=context.request.project_id,
            precondition_id=context.precondition.id,
            passed=value,
            evidence_refs=(f"check://{self.calls}",),
            checked_at=NOW,
        )


class PassingResultValidator:
    async def validate(self, *, request, result, workspace) -> OperatorDisciplineReport:
        return OperatorDisciplineReport(
            project_id=request.project_id,
            work_id=request.work_id,
            run_id=request.run_id,
            unsupported_claims=(),
            scope_violations=(),
            permission_violations=(),
            risk_mismatches=(),
            unnecessary_changes=(),
            output_tokens=None,
            changed_files=0,
            diff_lines=0,
            verdict="pass",
        )


class BlockingResultValidator(PassingResultValidator):
    async def validate(self, *, request, result, workspace) -> OperatorDisciplineReport:
        report = await super().validate(
            request=request,
            result=result,
            workspace=workspace,
        )
        return report.model_copy(
            update={
                "scope_violations": ("undeclared change: outside.txt",),
                "verdict": "blocked",
            }
        )


@pytest.fixture
async def work_store(dialect_engine) -> WorkStore:  # noqa: F811
    store = WorkStore(engine=dialect_engine)
    await store.init()
    return store


def _controller(
    work_store: WorkStore,
    durability_store: InMemoryStore,
    *,
    permission_policy: PermissionPolicy | None = None,
    checks=None,
    result_validator=None,
    heartbeat_interval: float = 0.01,
) -> OperatorController:
    return OperatorController(
        work_store=work_store,
        durability_store=durability_store,
        permission_policy=permission_policy or PermissionPolicy(),
        control_checks=checks or {},
        result_validator=result_validator or PassingResultValidator(),
        heartbeat_interval=heartbeat_interval,
    )


@pytest.mark.asyncio
async def test_declared_intents_are_evaluated_before_runtime_and_capabilities_are_scoped(
    work_store: WorkStore,
) -> None:
    runtime = RecordingRuntime()
    denied = _controller(
        work_store,
        InMemoryStore(),
        permission_policy=PermissionPolicy(deny_names=["workspace.write"]),
    )

    blocked = await denied.run(
        runtime=runtime,
        request=_request(),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert blocked.status == "blocked"
    assert runtime.started == 0
    assert [event.event_type for event in events] == [
        WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
        WorkEventType.WORK_BLOCKED,
    ]
    assert events[0].payload_json["permission_violations"] == [
        "Tool denied by name: workspace.write"
    ]
    assert events[0].payload_json["risk_mismatches"] == []
    assert events[0].payload_json["changed_files"] is None
    assert events[0].payload_json["diff_lines"] is None
    assert events[0].payload_json["output_tokens"] is None
    assert events[0].payload_json["verdict"] == "blocked"

    allowed_runtime = RecordingRuntime()
    passed = await _controller(work_store, InMemoryStore()).run(
        runtime=allowed_runtime,
        request=_request(run_id="run-2"),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    assert passed.status == "passed"
    assert [grant.name for grant in allowed_runtime.capabilities.grants] == ["filesystem.write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("violation_kind", ("missing_grant", "missing_permission"))
async def test_missing_capability_authority_is_recorded_before_blocking(
    work_store: WorkStore,
    violation_kind: str,
) -> None:
    capabilities = _capabilities()
    if violation_kind == "missing_grant":
        capabilities = capabilities.model_copy(update={"grants": ()})
        expected = "action-1 has no capability grant"
    else:
        grant = capabilities.grants[0].model_copy(update={"permissions": ()})
        capabilities = capabilities.model_copy(update={"grants": (grant,)})
        expected = "action-1 permission is outside the capability grant"
    runtime = RecordingRuntime()

    result = await _controller(work_store, InMemoryStore()).run(
        runtime=runtime,
        request=_request(),
        capsule=_capsule(),
        capabilities=capabilities,
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert result.status == "blocked"
    assert runtime.started == 0
    assert [event.event_type for event in events] == [
        WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
        WorkEventType.WORK_BLOCKED,
    ]
    assert events[0].payload_json["permission_violations"] == [expected]


@pytest.mark.asyncio
async def test_intent_above_accepted_contract_risk_is_recorded_before_runtime(
    work_store: WorkStore,
) -> None:
    request = _request().model_copy(
        update={"action_intents": (_intent().model_copy(update={"risk": "high"}),)}
    )
    runtime = RecordingRuntime()

    result = await _controller(work_store, InMemoryStore()).run(
        runtime=runtime,
        request=request,
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert result.status == "blocked"
    assert runtime.started == 0
    assert [event.event_type for event in events] == [
        WorkEventType.OPERATOR_DISCIPLINE_RECORDED,
        WorkEventType.WORK_BLOCKED,
    ]
    assert events[0].payload_json["permission_violations"] == []
    assert events[0].payload_json["risk_mismatches"] == [
        "action-1 risk high exceeds accepted contract risk low"
    ]


@pytest.mark.asyncio
async def test_all_intent_discipline_failures_are_recorded_together(
    work_store: WorkStore,
) -> None:
    request = _request().model_copy(
        update={"action_intents": (_intent().model_copy(update={"risk": "high"}),)}
    )
    runtime = RecordingRuntime()
    controller = _controller(
        work_store,
        InMemoryStore(),
        permission_policy=PermissionPolicy(deny_names=["workspace.write"]),
    )

    result = await controller.run(
        runtime=runtime,
        request=request,
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert result.status == "blocked"
    assert runtime.started == 0
    assert events[0].payload_json["permission_violations"] == [
        "Tool denied by name: workspace.write"
    ]
    assert events[0].payload_json["risk_mismatches"] == [
        "action-1 risk high exceeds accepted contract risk low"
    ]


@pytest.mark.asyncio
async def test_failing_authority_precondition_never_starts_runtime(
    work_store: WorkStore,
) -> None:
    precondition = _precondition(ControlPreconditionKind.AUTHORITY, "check://authority")
    runtime = RecordingRuntime()
    controller = _controller(
        work_store,
        InMemoryStore(),
        checks={"check://authority": SequenceControlCheck(False)},
    )

    result = await controller.run(
        runtime=runtime,
        request=_request(preconditions=(precondition,)),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert result.status == "blocked"
    assert runtime.started == 0
    assert [event.event_type for event in events] == [WorkEventType.CONTROL_DEGRADED]
    assert events[0].payload_json["failed_preconditions"] == [precondition.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ControlPreconditionKind))
async def test_each_control_precondition_kind_uses_its_deterministic_check(
    work_store: WorkStore,
    kind: ControlPreconditionKind,
) -> None:
    check_ref = f"check://{kind.value}"
    check = SequenceControlCheck(True)
    runtime = RecordingRuntime()

    result = await _controller(
        work_store,
        InMemoryStore(),
        checks={check_ref: check},
    ).run(
        runtime=runtime,
        request=_request(preconditions=(_precondition(kind, check_ref),)),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    assert result.status == "passed"
    assert runtime.started == 1
    assert check.calls == 1


@pytest.mark.asyncio
async def test_post_run_blocking_report_rejects_runtime_result(
    work_store: WorkStore,
) -> None:
    result = await _controller(
        work_store,
        InMemoryStore(),
        result_validator=BlockingResultValidator(),
    ).run(
        runtime=RecordingRuntime(),
        request=_request(),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    report_event = next(
        event for event in events if event.event_type is WorkEventType.OPERATOR_DISCIPLINE_RECORDED
    )
    assert result.status == "blocked"
    assert report_event.payload_json["scope_violations"] == ["undeclared change: outside.txt"]


@pytest.mark.asyncio
async def test_completed_run_returns_persisted_result_without_reexecution(
    work_store: WorkStore,
) -> None:
    durability = InMemoryStore()
    controller = _controller(work_store, durability)
    runtime = RecordingRuntime()
    request = _request(run_id="run-completed")

    first = await controller.run(
        runtime=runtime,
        request=request,
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )
    second = await controller.run(
        runtime=runtime,
        request=request,
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    assert first == second
    assert runtime.started == 1
    assert sum(event.event_type is WorkEventType.STAGE_STARTED for event in events) == 1
    assert sum(event.event_type is WorkEventType.EXECUTION_RECORDED for event in events) == 1


@pytest.mark.asyncio
async def test_stage_started_records_capsule_efficiency_measurements(
    work_store: WorkStore,
) -> None:
    capsule = _capsule()

    await _controller(work_store, InMemoryStore()).run(
        runtime=RecordingRuntime(),
        request=_request(),
        capsule=capsule,
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    started = next(event for event in events if event.event_type is WorkEventType.STAGE_STARTED)
    assert started.payload_json["knowledge_items_considered"] == 4
    assert started.payload_json["knowledge_items_selected"] == 0
    assert started.payload_json["artifact_bytes_referenced"] == 123
    assert started.payload_json["capsule_size_bytes"] == len(
        json.dumps(capsule.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    )


@pytest.mark.asyncio
async def test_inflight_observability_loss_freezes_until_control_is_restored(
    work_store: WorkStore,
) -> None:
    precondition = _precondition(
        ControlPreconditionKind.OBSERVABILITY,
        "check://observability",
    )
    check = SequenceControlCheck(True, False, False, True)
    durability = InMemoryStore()
    controller = _controller(
        work_store,
        durability,
        checks={"check://observability": check},
    )
    first_runtime = RecordingRuntime(block=True)

    first = await controller.run(
        runtime=first_runtime,
        request=_request(run_id="run-1", preconditions=(precondition,)),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )
    omitted_runtime = RecordingRuntime()
    omitted = await controller.run(
        runtime=omitted_runtime,
        request=_request(run_id="run-omitted"),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )
    frozen_runtime = RecordingRuntime()
    frozen = await controller.run(
        runtime=frozen_runtime,
        request=_request(run_id="run-2", preconditions=(precondition,)),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )
    restored_runtime = RecordingRuntime()
    restored = await controller.run(
        runtime=restored_runtime,
        request=_request(run_id="run-3", preconditions=(precondition,)),
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )

    events = await work_store.read_events("work-1", project_id="project-a")
    control_events = [
        event.event_type
        for event in events
        if event.event_type
        in {
            WorkEventType.CONTROL_DEGRADED,
            WorkEventType.CONTROL_RESTORED,
        }
    ]
    assert first.status == "blocked"
    assert first_runtime.cancelled is True
    assert omitted.status == "blocked"
    assert omitted_runtime.started == 0
    assert frozen.status == "blocked"
    assert frozen_runtime.started == 0
    assert restored.status == "passed"
    assert restored_runtime.started == 1
    assert control_events == [
        WorkEventType.CONTROL_DEGRADED,
        WorkEventType.CONTROL_RESTORED,
    ]


@pytest.mark.asyncio
async def test_cancelled_execution_leaves_durable_running_state_for_retry(
    work_store: WorkStore,
) -> None:
    durability = InMemoryStore()
    controller = _controller(work_store, durability, heartbeat_interval=60)
    runtime = RecordingRuntime(block=True)
    request = _request(run_id="run-killed")
    execution = asyncio.create_task(
        controller.run(
            runtime=runtime,
            request=request,
            capsule=_capsule(),
            capabilities=_capabilities(),
            workspace=None,
        )
    )
    await runtime.started_event.wait()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    durable = await durability.load_run("work:work-1:implement", "run-killed")
    events = await work_store.read_events("work-1", project_id="project-a")
    assert durable is not None
    assert durable.status is StepStatus.RUNNING
    assert [event.event_type for event in events] == [WorkEventType.STAGE_STARTED]

    retry = await _controller(work_store, durability).run(
        runtime=RecordingRuntime(),
        request=request,
        capsule=_capsule(),
        capabilities=_capabilities(),
        workspace=None,
    )
    assert retry.status == "passed"
