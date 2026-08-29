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
from pathlib import Path

import pytest
from pydantic import ValidationError

from sagewai.fleet import (
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
    FleetOperatorRuntime,
    NoCompatibleWorkerError,
    OperatorResult,
    TaskCapsule,
    WorkContract,
    WorkItem,
    WorkRequest,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class _Workspace:
    ref = "workspace://attempt-1"
    project_id = "project-a"
    work_id = "work-1"
    path = Path("/worker-local/sagewai/work-1")


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


async def _compatible_registry() -> InMemoryFleetRegistry:
    registry = InMemoryFleetRegistry()
    worker = await registry.register_worker(
        "claude-worker",
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
    capsule = _capsule()
    capabilities = _capabilities()
    workspace = _Workspace()

    first_waiter = asyncio.create_task(
        runtime.run(request, capsule, capabilities, workspace=workspace)
    )
    await _wait_for_status(store, "pending")
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    # A retry reattaches to the existing run_id instead of enqueuing a second task.
    waiter = asyncio.create_task(runtime.run(request, capsule, capabilities, workspace=workspace))
    await asyncio.sleep(0)
    assert len(await store.list_tasks(org_id="org-a", project_id="project-a")) == 1

    codex_worker = WorkerCapabilities(
        capability_names=["runtime.codex", "cli.git"],
    )
    assert (
        await store.claim_task(
            "worker-codex",
            "org-a",
            [],
            "default",
            codex_worker.routing_labels(),
            project_id="project-a",
        )
        is None
    )

    claude_worker = WorkerCapabilities(
        capability_names=["runtime.claude", "cli.git"],
    )
    claimed = await store.claim_task(
        "worker-claude-1",
        "org-a",
        [],
        "default",
        claude_worker.routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    assert claimed["run_id"] == request.run_id
    assert claimed["payload"]["required_capabilities"] == [
        "runtime.claude",
        "cli.git",
    ]
    assert claimed["payload"]["workspace_ref"] == workspace.ref
    payload_json = json.dumps(claimed["payload"])
    assert str(workspace.path) not in payload_json
    assert "worker-secret-value" not in payload_json

    # The first worker disappears. Existing lease/reaper behavior preserves the
    # durable task and lets a second compatible worker resume the same run.
    store._claimed[request.run_id]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    assert await store.reap_expired_leases() == {"failed": 0, "requeued": 1}
    resumed = await store.claim_task(
        "worker-claude-2",
        "org-a",
        [],
        "default",
        claude_worker.routing_labels(),
        project_id="project-a",
    )
    assert resumed is not None
    assert resumed["run_id"] == request.run_id
    assert resumed["payload"] == claimed["payload"]

    expected = _result()
    await store.report_task(
        request.run_id,
        "completed",
        expected.model_dump_json(),
        None,
        worker_id="worker-claude-2",
    )
    actual = await asyncio.wait_for(waiter, timeout=1)
    assert actual == expected
    persisted = await store.get_task(
        request.run_id,
        org_id="org-a",
        project_id="project-a",
    )
    assert persisted is not None
    assert OperatorResult.model_validate_json(persisted["output"]) == expected


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
    worker = WorkerCapabilities(capability_names=["runtime.claude", "cli.git"])
    await store.claim_task(
        "worker-claude",
        "org-a",
        [],
        "default",
        worker.routing_labels(),
        project_id="project-a",
    )
    await store.report_task(
        "run-1",
        "completed",
        _result(run_id="another-run").model_dump_json(),
        None,
        worker_id="worker-claude",
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
    worker = WorkerCapabilities(capability_names=["runtime.claude", "cli.git"])
    await store.claim_task(
        "worker-claude",
        "org-a",
        [],
        "default",
        worker.routing_labels(),
        project_id="project-a",
    )
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
        json.dumps(hostile_result),
        None,
        worker_id="worker-claude",
    )

    with pytest.raises(ValidationError, match="action result belongs to a different project"):
        await asyncio.wait_for(waiter, timeout=1)
