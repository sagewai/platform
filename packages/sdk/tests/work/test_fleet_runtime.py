# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Work stage dispatch through the existing durable Fleet queue."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerCapabilities,
    WorkerRecord,
)
from sagewai.fleet.models import WORK_CAPABILITY_LABEL_PREFIX
from sagewai.work import (
    AcceptanceCriterion,
    ActionScope,
    CapabilityGrant,
    CapabilitySet,
    FleetOperatorResultEnvelope,
    FleetOperatorRuntime,
    NoCompatibleWorkerError,
    OperatorResult,
    TaskCapsule,
    WorkContract,
    WorkItem,
    WorkRequest,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _request() -> WorkRequest:
    return WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="review",
        action_scope=ActionScope(
            project_id="project-a",
            objective="Review the accepted change",
            allowed_targets=("packages/sdk/sagewai/work",),
            allowed_capabilities=("cli.git",),
        ),
        action_intents=(),
        control_preconditions=(),
    )


def _capsule() -> TaskCapsule:
    item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref="source://task",
        title="Fleet review",
        description="Review on a compatible worker",
        created_at=NOW,
    )
    contract = WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Review on a compatible worker",
        allowed_scope=("packages/sdk/sagewai/work",),
        acceptance_criteria=(
            AcceptanceCriterion(
                id="criterion-structured-result",
                project_id="project-a",
                statement="structured result returned",
                verification_kind="deterministic",
            ),
        ),
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
        stage="review",
        work_item=item,
        contract=contract,
        knowledge_refs=(),
        knowledge_items=(),
        knowledge_items_considered=0,
        artifact_bytes_referenced=0,
        open_assumption_ids=(),
        prior_result_refs=(),
    )


def _capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="cli.git",
                kind="cli",
                scope={"repository": "sagewai/platform"},
                permissions=("read",),
                credential_ref="credential://worker-local/git",
            ),
        ),
    )


async def _compatible_registry(*, workers: int = 1) -> InMemoryFleetRegistry:
    registry = InMemoryFleetRegistry()
    for index in range(workers):
        worker = await registry.register_worker(
            f"claude-worker-{index + 1}",
            "org-a",
            WorkerCapabilities(capability_names=["runtime.claude", "cli.git"]),
            project_id="project-a",
            secret_hash="worker-local-secret-hash",
        )
        await registry.approve_worker(worker.id, approved_by="test")
        await registry.heartbeat(worker.id)
    return registry


def _result(*, run_id: str = "run-1") -> OperatorResult:
    return OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id=run_id,
        status="passed",
        summary="independent review passed",
        evidence_refs=("fleet://worker-claude/result",),
        artifact_refs=(),
        changes=(),
        verification=("review contract",),
        risks=(),
        action_results=(),
    )


async def _wait_for_status(store: InMemoryTaskStore, status: str) -> None:
    for _ in range(100):
        task = await store.get_task("run-1", org_id="org-a", project_id="project-a")
        if task is not None and task["status"] == status:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"run-1 did not reach {status}")


def test_worker_capabilities_reserve_work_routing_labels() -> None:
    with pytest.raises(ValidationError, match="reserved for Work capability routing"):
        WorkerCapabilities(
            labels={f"{WORK_CAPABILITY_LABEL_PREFIX}runtime.claude": "true"},
        )

    capabilities = WorkerCapabilities(
        capability_names=["runtime.claude", "cli.git"],
        labels={"region": "eu"},
    )
    assert capabilities.routing_labels() == {
        "region": "eu",
        f"{WORK_CAPABILITY_LABEL_PREFIX}runtime.claude": "true",
        f"{WORK_CAPABILITY_LABEL_PREFIX}cli.git": "true",
    }


def test_worker_online_state_requires_caller_supplied_ttl() -> None:
    worker = WorkerRecord(
        id="worker-1",
        name="claude-worker",
        org_id="org-a",
        project_id="project-a",
        capabilities=WorkerCapabilities(capability_names=["runtime.claude"]),
        registered_at=NOW - timedelta(hours=1),
        last_heartbeat=NOW - timedelta(seconds=20),
    )

    assert worker.is_online(now=NOW, heartbeat_ttl=timedelta(seconds=30))
    assert not worker.is_online(now=NOW, heartbeat_ttl=timedelta(seconds=10))
    with pytest.raises(TypeError):
        worker.is_online(now=NOW)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_fleet_runtime_rejects_no_compatible_worker() -> None:
    store = InMemoryTaskStore()
    registry = InMemoryFleetRegistry()
    incompatible = await registry.register_worker(
        "codex-worker",
        "org-a",
        WorkerCapabilities(capability_names=["runtime.codex", "cli.git"]),
        project_id="project-a",
        secret_hash="codex-secret-hash",
    )
    foreign = await registry.register_worker(
        "foreign-claude-worker",
        "org-a",
        WorkerCapabilities(capability_names=["runtime.claude", "cli.git"]),
        project_id="project-b",
        secret_hash="foreign-secret-hash",
    )
    for worker in (incompatible, foreign):
        await registry.approve_worker(worker.id, approved_by="test")
        await registry.heartbeat(worker.id)
    runtime = FleetOperatorRuntime(
        store=store,
        registry=registry,
        org_id="org-a",
        runtime_capability="runtime.claude",
        poll_interval_seconds=0.001,
        heartbeat_ttl=timedelta(seconds=30),
    )

    with pytest.raises(
        NoCompatibleWorkerError,
        match="no approved online worker satisfies",
    ):
        await runtime.run(_request(), _capsule(), _capabilities(), workspace=None)

    assert await store.list_tasks(org_id="org-a", project_id="project-a") == []


@pytest.mark.asyncio
async def test_fleet_runtime_matches_capabilities_and_resumes_after_worker_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "worker-secret-value")
    store = InMemoryTaskStore(lease_ttl_seconds=0.01)
    registry = await _compatible_registry(workers=2)
    workers = sorted(
        await registry.list_workers("org-a", project_id="project-a"),
        key=lambda worker: (worker.registered_at, worker.id),
    )
    first_worker, replacement_worker = workers
    runtime = FleetOperatorRuntime(
        store=store,
        registry=registry,
        org_id="org-a",
        runtime_capability="runtime.claude",
        poll_interval_seconds=0.001,
        heartbeat_ttl=timedelta(seconds=30),
    )
    request = _request()
    capsule = _capsule()
    capabilities = _capabilities()

    first_waiter = asyncio.create_task(runtime.run(request, capsule, capabilities, workspace=None))
    await _wait_for_status(store, "pending")
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    waiter = asyncio.create_task(runtime.run(request, capsule, capabilities, workspace=None))
    await asyncio.sleep(0)
    assert len(await store.list_tasks(org_id="org-a", project_id="project-a")) == 1

    dispatcher = FleetDispatcher(store=store, poll_interval=0.001, poll_timeout=0.01)
    assert (
        await dispatcher.claim(
            worker_id="not-selected",
            org_id="org-a",
            models_canonical=[],
            labels=WorkerCapabilities(
                capability_names=["runtime.claude", "cli.git"]
            ).routing_labels(),
            project_id="project-a",
        )
        is None
    )

    claimed = await dispatcher.claim(
        worker_id=first_worker.id,
        org_id="org-a",
        models_canonical=[],
        labels=first_worker.capabilities.routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    assert claimed["run_id"] == request.run_id
    assert claimed["payload"]["required_capabilities"] == [
        "runtime.claude",
        "cli.git",
    ]
    assert claimed["payload"]["workspace"] is None
    payload_json = json.dumps(claimed["payload"])
    assert "credential://" not in payload_json
    assert "worker-secret-value" not in payload_json

    store._claimed[("org-a", "p:project-a", request.run_id)]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    assert await store.reap_expired_leases() == {"failed": 0, "requeued": 1}
    registry._workers[first_worker.id] = first_worker.model_copy(
        update={"last_heartbeat": NOW - timedelta(minutes=5)}
    )

    for _ in range(100):
        status = await store.get_task(request.run_id, org_id="org-a", project_id="project-a")
        if status is not None and status["selected_worker_id"] == replacement_worker.id:
            break
        await asyncio.sleep(0.001)
    else:
        raise AssertionError("Fleet task was not retargeted after worker loss")

    resumed = await dispatcher.claim(
        worker_id=replacement_worker.id,
        org_id="org-a",
        models_canonical=[],
        labels=replacement_worker.capabilities.routing_labels(),
        project_id="project-a",
    )
    assert resumed is not None
    assert resumed["run_id"] == request.run_id
    assert resumed["payload"] == claimed["payload"]

    expected = _result()
    await store.report_task(
        request.run_id,
        "completed",
        FleetOperatorResultEnvelope(
            result=expected,
            workspace_result=None,
        ).model_dump_json(),
        None,
        worker_id=replacement_worker.id,
        org_id="org-a",
        project_id="project-a",
    )
    actual = await asyncio.wait_for(waiter, timeout=1)
    assert actual == expected


@pytest.mark.asyncio
async def test_fleet_runtime_rejects_result_for_another_run() -> None:
    store = InMemoryTaskStore()
    registry = await _compatible_registry()
    runtime = FleetOperatorRuntime(
        store=store,
        registry=registry,
        org_id="org-a",
        runtime_capability="runtime.claude",
        poll_interval_seconds=0.001,
        heartbeat_ttl=timedelta(seconds=30),
    )
    waiter = asyncio.create_task(
        runtime.run(_request(), _capsule(), _capabilities(), workspace=None)
    )
    await _wait_for_status(store, "pending")
    status = await store.get_task("run-1", org_id="org-a", project_id="project-a")
    assert status is not None
    worker_id = status["selected_worker_id"]
    claimed = await FleetDispatcher(store=store, poll_interval=0.001, poll_timeout=0.01).claim(
        worker_id=worker_id,
        org_id="org-a",
        models_canonical=[],
        labels=WorkerCapabilities(capability_names=["runtime.claude", "cli.git"]).routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    await store.report_task(
        "run-1",
        "completed",
        FleetOperatorResultEnvelope(
            result=_result(run_id="another-run"), workspace_result=None
        ).model_dump_json(),
        None,
        worker_id=worker_id,
        org_id="org-a",
        project_id="project-a",
    )

    with pytest.raises(ValueError, match="different request"):
        await asyncio.wait_for(waiter, timeout=1)


@pytest.mark.asyncio
async def test_fleet_runtime_rejects_serialized_nested_result_from_another_project() -> None:
    store = InMemoryTaskStore()
    registry = await _compatible_registry()
    runtime = FleetOperatorRuntime(
        store=store,
        registry=registry,
        org_id="org-a",
        runtime_capability="runtime.claude",
        poll_interval_seconds=0.001,
        heartbeat_ttl=timedelta(seconds=30),
    )
    waiter = asyncio.create_task(
        runtime.run(_request(), _capsule(), _capabilities(), workspace=None)
    )
    await _wait_for_status(store, "pending")
    status = await store.get_task("run-1", org_id="org-a", project_id="project-a")
    assert status is not None
    worker_id = status["selected_worker_id"]
    claimed = await FleetDispatcher(store=store, poll_interval=0.001, poll_timeout=0.01).claim(
        worker_id=worker_id,
        org_id="org-a",
        models_canonical=[],
        labels=WorkerCapabilities(capability_names=["runtime.claude", "cli.git"]).routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    hostile_result = _result().model_dump(mode="json")
    hostile_result["action_results"] = [
        {
            "project_id": None,
            "action_id": "action-1",
            "status": "succeeded",
            "external_ref": None,
            "evidence_refs": [],
            "started_at": NOW.isoformat(),
            "completed_at": NOW.isoformat(),
        }
    ]
    await store.report_task(
        "run-1",
        "completed",
        json.dumps(
            {"kind": "work.operator.result", "result": hostile_result, "workspace_result": None}
        ),
        None,
        worker_id=worker_id,
        org_id="org-a",
        project_id="project-a",
    )

    with pytest.raises(ValidationError, match="action result belongs to a different project"):
        await asyncio.wait_for(waiter, timeout=1)


@pytest.mark.asyncio
async def test_fleet_runtime_returns_durable_failed_result_after_attempt_exhaustion() -> None:
    store = InMemoryTaskStore(lease_ttl_seconds=0.01, max_attempts=1)
    registry = await _compatible_registry()
    runtime = FleetOperatorRuntime(
        store=store,
        registry=registry,
        org_id="org-a",
        runtime_capability="runtime.claude",
        poll_interval_seconds=0.001,
        heartbeat_ttl=timedelta(seconds=30),
    )
    request = _request()
    waiter = asyncio.create_task(
        runtime.run(request, _capsule(), _capabilities(), workspace=None)
    )
    await _wait_for_status(store, "pending")
    status = await store.get_task(
        request.run_id, org_id="org-a", project_id="project-a"
    )
    assert status is not None
    worker_id = status["selected_worker_id"]
    claimed = await FleetDispatcher(
        store=store, poll_interval=0.001, poll_timeout=0.01
    ).claim(
        worker_id=worker_id,
        org_id="org-a",
        models_canonical=[],
        labels=WorkerCapabilities(
            capability_names=["runtime.claude", "cli.git"]
        ).routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    store._claimed[("org-a", "p:project-a", request.run_id)][
        "lease_expires_at"
    ] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await store.reap_expired_leases() == {"failed": 1, "requeued": 0}

    failed = await asyncio.wait_for(waiter, timeout=1)
    assert failed == OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id=request.run_id,
        status="failed",
        summary="lease expired after max attempts",
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={},
    )
    assert await runtime.run(
        request, _capsule(), _capabilities(), workspace=None
    ) == failed
    assert len(await store.list_tasks(org_id="org-a", project_id="project-a")) == 1
