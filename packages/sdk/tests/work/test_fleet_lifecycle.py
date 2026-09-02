# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fleet worker loss through the accepted software Work lifecycle."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.encoders import jsonable_encoder

from sagewai.artifacts import LocalArtifactStore
from sagewai.core.state import InMemoryStore
from sagewai.fleet import (
    FleetDispatcher,
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerCapabilities,
)
from sagewai.fleet.runner import WorkerRunner
from sagewai.work import (
    FleetOperatorResultEnvelope,
    OperatorResult,
    ReviewResult,
    TaskCapsuleCompiler,
    WorkEventType,
    WorkStore,
)
from sagewai.work.knowledge import KnowledgeStore
from sagewai.work.profiles.software import (
    SoftwareLifecycle,
    SoftwareProfile,
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareStageOperator,
    SoftwareVerifier,
    SoftwareWorktreeManager,
    StageOperatorLadder,
)
from sagewai.work.profiles.software.fleet_worker import (
    SoftwareFleetTaskHandler,
    SoftwareFleetWorkspaceResolver,
)
from sagewai.work.profiles.software.fleet_workspace import (
    SoftwareFleetWorkspaceTransport,
    software_repository_ref,
)
from sagewai.work.runtime import ClaudeRuntime
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.fakes_verification import LocalVerificationRunner
from tests.work.test_lifecycle import (
    AnalysisRuntime,
    MutationRuntime,
    _always_pass_command,
    _contract,
    _controller,
    _read_capabilities,
    _repository,
    _work_item,
    _write_capabilities,
)


async def _register_worker(
    registry: InMemoryFleetRegistry,
    *,
    name: str,
    project_id: str,
    capabilities: tuple[str, ...],
):
    worker = await registry.register_worker(
        name,
        "org-a",
        WorkerCapabilities(capability_names=list(capabilities)),
        project_id=project_id,
        secret_hash=f"secret-hash-{name}",
    )
    worker = await registry.approve_worker(worker.id, approved_by="test")
    await registry.heartbeat(worker.id)
    return worker


async def _wait_for_task(store: InMemoryTaskStore, run_id: str) -> dict:
    for _ in range(1000):
        task = await store.get_task(
            run_id,
            org_id="org-a",
            project_id="project-a",
        )
        if task is not None:
            return task
        await asyncio.sleep(0.001)
    raise AssertionError(f"{run_id} was not enqueued")


async def _wait_for_selected_worker(
    store: InMemoryTaskStore,
    run_id: str,
    worker_id: str,
) -> None:
    for _ in range(1000):
        task = await store.get_task(
            run_id,
            org_id="org-a",
            project_id="project-a",
        )
        if task is not None and task.get("selected_worker_id") == worker_id:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"{run_id} was not selected for {worker_id}")


def _accepted_review(run_id: str) -> OperatorResult:
    review = ReviewResult(
        project_id="project-a",
        attempt_id=run_id,
        verdict="accept",
        findings=(),
        evidence_refs=(f"fleet://{run_id}",),
        introduced_assumptions=(),
        unsupported_claims=(),
        scope_expansions=(),
        unsupported_implementation_choices=(),
    )
    return OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id=run_id,
        status="passed",
        summary="independent Fleet review passed",
        evidence_refs=review.evidence_refs,
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={"review_result": review.model_dump(mode="json")},
    )


class _ReviewClaudeRuntime(ClaudeRuntime):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls.append(request.run_id)
        return _accepted_review(request.run_id)


def _clone_worker_repository(repository: Path, destination: Path) -> Path:
    subprocess.run(
        ("git", "clone", "-q", str(repository), str(destination)),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(destination),
            "remote",
            "set-url",
            "origin",
            "https://github.com/sagewai/platform.git",
        ),
        check=True,
    )
    return destination


def _worker_runner(
    *,
    worker,
    registry: InMemoryFleetRegistry,
    dispatcher: FleetDispatcher,
    task_store: InMemoryTaskStore,
    task_handler,
) -> WorkerRunner:
    async def gateway(request: httpx.Request) -> httpx.Response:
        worker_id = request.headers.get("X-Worker-Id", "")
        record = await registry.get_worker(worker_id)
        if record is None:
            return httpx.Response(404, request=request)
        if request.url.path == "/api/v1/fleet/claim":
            body = json.loads(request.content or b"{}")
            task = await dispatcher.claim(
                worker_id=worker_id,
                org_id=record.org_id,
                models_canonical=record.capabilities.models_canonical,
                pool=record.capabilities.pool,
                labels=record.capabilities.routing_labels(),
                project_id=record.project_id,
                poll_timeout=body.get("poll_timeout", 0.01),
            )
            if task is None:
                return httpx.Response(204, request=request)
            return httpx.Response(200, json=jsonable_encoder(task), request=request)
        if request.url.path == "/api/v1/fleet/report":
            body = json.loads(request.content)
            await dispatcher.report(
                worker_id=worker_id,
                org_id=record.org_id,
                project_id=record.project_id,
                run_id=body["run_id"],
                status=body["status"],
                output=body.get("output"),
                error=body.get("error"),
            )
            return httpx.Response(200, json={}, request=request)
        if request.url.path == "/api/v1/fleet/heartbeat":
            await registry.heartbeat(worker_id)
            await task_store.renew_worker_leases(worker_id)
            return httpx.Response(200, json={}, request=request)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway),
        base_url="http://fleet.test",
    )
    return WorkerRunner(
        base_url="http://fleet.test",
        project=worker.project_id,
        capability_names=list(worker.capabilities.capability_names),
        worker_id=worker.id,
        worker_secret=f"worker-local-secret-{worker.id}",
        http_client=client,
        task_handler=task_handler,
        poll_timeout=0.01,
        heartbeat_interval=0.005,
    )


@pytest.mark.parametrize(
    "task_attempts_exhausted",
    [False, True],
    ids=["retarget", "escalates-after-task-attempts-exhausted"],
)
@pytest.mark.asyncio
async def test_fleet_worker_loss_resumes_only_unfinished_software_stage(
    task_attempts_exhausted: bool,
    monkeypatch: pytest.MonkeyPatch,
    dialect_engine,  # noqa: F811
    tmp_path,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "worker-local-token")
    work_store = WorkStore(engine=dialect_engine)
    knowledge_store = KnowledgeStore(engine=dialect_engine)
    await work_store.init()
    await knowledge_store.init()
    durability = InMemoryStore()
    task_store = InMemoryTaskStore(
        lease_ttl_seconds=0.01, max_attempts=1 if task_attempts_exhausted else 3
    )
    registry = InMemoryFleetRegistry()
    incompatible = await _register_worker(
        registry,
        name="codex-worker",
        project_id="project-a",
        capabilities=("runtime.codex", "filesystem.read"),
    )
    foreign = await _register_worker(
        registry,
        name="foreign-claude-worker",
        project_id="project-b",
        capabilities=("runtime.claude", "filesystem.read"),
    )
    repository, base_sha = _repository(tmp_path)
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "remote",
            "add",
            "origin",
            "https://github.com/sagewai/platform.git",
        ),
        check=True,
    )
    workspace_transport = SoftwareFleetWorkspaceTransport(
        repository_ref=await software_repository_ref(repository),
    )
    analyzer = AnalysisRuntime()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    artifact_store = LocalArtifactStore(root=tmp_path / "objects")
    analyst = StageOperatorLadder(
        (
            SoftwareStageOperator(
                actor_ref="operator:analyst",
                runtime=analyzer,
                capabilities=_read_capabilities(),
                controller=_controller(
                    work_store,
                    durability,
                    SoftwareReadOnlyResultValidator(),
                ),
            ),
        )
    )
    lifecycle = SoftwareLifecycle(
        profile=SoftwareProfile(),
        work_store=work_store,
        knowledge_store=knowledge_store,
        capsule_compiler=TaskCapsuleCompiler(
            knowledge_store=knowledge_store,
            artifact_store=artifact_store,
        ),
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worktrees"),
        verifier=SoftwareVerifier(
            knowledge_store=knowledge_store,
            runner=LocalVerificationRunner(),
            artifact_store=artifact_store,
        ),
        artifact_store=artifact_store,
        repository=repository,
        analyst=analyst,
        designer=analyst,
        implementer=StageOperatorLadder(
            (
                SoftwareStageOperator(
                    actor_ref="operator:implementer",
                    runtime=implementer,
                    capabilities=_write_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        SoftwareResultValidator(),
                    ),
                ),
            )
        ),
        reviewer=StageOperatorLadder(
            (
                SoftwareStageOperator.fleet(
                    actor_ref="fleet:reviewer",
                    store=task_store,
                    registry=registry,
                    org_id="org-a",
                    runtime_capability="runtime.claude",
                    poll_interval_seconds=0.001,
                    heartbeat_ttl=timedelta(seconds=30),
                    workspace_transport=workspace_transport,
                    capabilities=_read_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        SoftwareReadOnlyResultValidator(),
                    ),
                ),
            )
        ),
        repairer=StageOperatorLadder(
            (
                SoftwareStageOperator(
                    actor_ref="operator:implementer",
                    runtime=repairer,
                    capabilities=_write_capabilities(),
                    controller=_controller(
                        work_store,
                        durability,
                        SoftwareResultValidator(),
                    ),
                ),
            )
        ),
        repo_instructions=("AGENTS.md",),
        verification_commands=(_always_pass_command(),),
        max_attempts_per_stage=3,
    )

    first = await _register_worker(
        registry,
        name="claude-worker-1",
        project_id="project-a",
        capabilities=("runtime.claude", "filesystem.read"),
    )
    replacement = await _register_worker(
        registry,
        name="claude-worker-2",
        project_id="project-a",
        capabilities=("runtime.claude", "filesystem.read"),
    )
    first_repository = _clone_worker_repository(
        repository,
        tmp_path / "worker-1-repository",
    )
    replacement_repository = _clone_worker_repository(
        repository,
        tmp_path / "worker-2-repository",
    )
    first_runtime = _ReviewClaudeRuntime()
    replacement_runtime = _ReviewClaudeRuntime()
    dispatcher = FleetDispatcher(
        task_store,
        poll_interval=0.001,
        poll_timeout=0.01,
    )
    first_runner = _worker_runner(
        worker=first,
        registry=registry,
        dispatcher=dispatcher,
        task_store=task_store,
        task_handler=SoftwareFleetTaskHandler(
            workspace_resolver=SoftwareFleetWorkspaceResolver(
                repository=first_repository,
                worktree_manager=SoftwareWorktreeManager(
                    root=tmp_path / "worker-1-worktrees"
                ),
            ),
            claude_review_runtime=first_runtime,
        ),
    )
    replacement_runner = _worker_runner(
        worker=replacement,
        registry=registry,
        dispatcher=dispatcher,
        task_store=task_store,
        task_handler=SoftwareFleetTaskHandler(
            workspace_resolver=SoftwareFleetWorkspaceResolver(
                repository=replacement_repository,
                worktree_manager=SoftwareWorktreeManager(
                    root=tmp_path / "worker-2-worktrees"
                ),
            ),
            claude_review_runtime=replacement_runtime,
        ),
    )
    started = asyncio.create_task(
        lifecycle.start(work_item=_work_item(), contract=_contract(base_sha))
    )
    run_id = "work-1:review:1"
    await _wait_for_task(task_store, run_id)

    assert (
        await task_store.claim_task(
            incompatible.id,
            "org-a",
            [],
            "default",
            incompatible.capabilities.routing_labels(),
            project_id="project-a",
        )
        is None
    )
    assert (
        await task_store.claim_task(
            foreign.id,
            "org-a",
            [],
            "default",
            foreign.capabilities.routing_labels(),
            project_id="project-b",
        )
        is None
    )
    claimed = await first_runner._claim()
    assert claimed is not None
    serialized_payload = json.dumps(claimed["payload"])
    assert "worker-local-token" not in serialized_payload
    assert "secret-hash-" not in serialized_payload

    task_store._claimed[("org-a", "p:project-a", run_id)]["lease_expires_at"] = datetime.now(
        timezone.utc
    ) - timedelta(seconds=1)
    await registry.revoke_worker(first.id)
    reaped = await task_store.reap_expired_leases()
    if task_attempts_exhausted:
        assert reaped == {"failed": 1, "requeued": 0}
        escalated_run_id = "work-1:review:2"
        await _wait_for_task(task_store, escalated_run_id)
        worker_result = await replacement_runner.run_once()
        assert worker_result == {
            "claimed": True,
            "run_id": escalated_run_id,
            "status": "completed",
            "reported": True,
        }
        record = await asyncio.wait_for(started, timeout=1)
        await first_runner.http_client.aclose()
        await replacement_runner.http_client.aclose()
        assert record.status == "READY_TO_MERGE"
        assert analyzer.calls == 1
        assert implementer.calls == 1
        assert first_runtime.calls == []
        assert replacement_runtime.calls == [escalated_run_id]
        events = await work_store.read_events("work-1", project_id="project-a")
        selected = [
            event.payload_json
            for event in events
            if event.event_type is WorkEventType.RUNTIME_SELECTED
            and event.payload_json["stage"] == "review"
        ]
        assert [(item["run_id"], item["reason"]) for item in selected] == [
            (run_id, "initial"),
            (escalated_run_id, "escalated"),
        ]
        assert any(
            event.event_type is WorkEventType.EXECUTION_RECORDED
            and event.payload_json.get("run_id") == run_id
            and event.payload_json.get("status") == "failed"
            for event in events
        )
        assert any(
            event.event_type is WorkEventType.REVIEW_RECORDED
            and event.payload_json.get("attempt_id") == escalated_run_id
            for event in events
        )
        return

    assert reaped == {"failed": 0, "requeued": 1}
    await _wait_for_selected_worker(task_store, run_id, replacement.id)
    pending = task_store._pending[0]
    assert pending["run_id"] == run_id
    assert pending["payload"] == claimed["payload"]
    expected = _accepted_review(run_id)
    worker_result = await replacement_runner.run_once()
    assert worker_result == {
        "claimed": True,
        "run_id": run_id,
        "status": "completed",
        "reported": True,
    }

    record = await asyncio.wait_for(started, timeout=1)
    await first_runner.http_client.aclose()
    await replacement_runner.http_client.aclose()
    assert record.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    assert implementer.calls == 1
    assert repairer.calls == 0
    assert first_runtime.calls == []
    assert replacement_runtime.calls == [run_id]
    events = await work_store.read_events("work-1", project_id="project-a")
    assert sum(
        event.event_type is WorkEventType.STAGE_COMPLETED
        and event.payload_json.get("stage") == "implement"
        for event in events
    ) == 1
    assert sum(
        event.event_type is WorkEventType.STAGE_STARTED
        and event.payload_json.get("run_id") == run_id
        for event in events
    ) == 1
    assert sum(
        event.event_type is WorkEventType.EXECUTION_RECORDED
        and event.payload_json.get("run_id") == run_id
        for event in events
    ) == 1
    review = next(
        event for event in events if event.event_type is WorkEventType.REVIEW_RECORDED
    )
    assert review.actor_ref == "fleet:reviewer"
    persisted = await task_store.get_task(
        run_id,
        org_id="org-a",
        project_id="project-a",
    )
    assert persisted is not None
    envelope = FleetOperatorResultEnvelope.model_validate_json(persisted["output"])
    assert envelope.result == expected
    assert envelope.workspace_result is not None
