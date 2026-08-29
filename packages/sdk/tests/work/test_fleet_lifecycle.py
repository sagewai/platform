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
from datetime import datetime, timedelta, timezone

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.core.state import InMemoryStore
from sagewai.fleet import (
    InMemoryFleetRegistry,
    InMemoryTaskStore,
    WorkerCapabilities,
)
from sagewai.work import (
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
)
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


@pytest.mark.asyncio
async def test_fleet_worker_loss_resumes_only_unfinished_software_stage(
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
    task_store = InMemoryTaskStore(lease_ttl_seconds=0.01)
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
    analyzer = AnalysisRuntime()
    implementer = MutationRuntime(implement_text="initial", repair_text="fixed")
    repairer = MutationRuntime(implement_text="unused", repair_text="fixed")
    artifact_store = LocalArtifactStore(root=tmp_path / "objects")
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
        analyst=SoftwareStageOperator(
            actor_ref="operator:analyst",
            runtime=analyzer,
            capabilities=_read_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareReadOnlyResultValidator(),
            ),
        ),
        implementer=SoftwareStageOperator(
            actor_ref="operator:implementer",
            runtime=implementer,
            capabilities=_write_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareResultValidator(),
            ),
        ),
        reviewer=SoftwareStageOperator.fleet(
            actor_ref="fleet:reviewer",
            store=task_store,
            registry=registry,
            org_id="org-a",
            runtime_capability="runtime.claude",
            poll_interval_seconds=0.001,
            heartbeat_ttl=timedelta(seconds=30),
            capabilities=_read_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareReadOnlyResultValidator(),
            ),
        ),
        repairer=SoftwareStageOperator(
            actor_ref="operator:implementer",
            runtime=repairer,
            capabilities=_write_capabilities(),
            controller=_controller(
                work_store,
                durability,
                SoftwareResultValidator(),
            ),
        ),
        repo_instructions=("AGENTS.md",),
        verification_commands=(_always_pass_command(),),
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
    claimed = await task_store.claim_task(
        first.id,
        "org-a",
        [],
        "default",
        first.capabilities.routing_labels(),
        project_id="project-a",
    )
    assert claimed is not None
    serialized_payload = json.dumps(claimed["payload"])
    assert "worker-local-token" not in serialized_payload
    assert "secret-hash-" not in serialized_payload

    task_store._claimed[run_id]["lease_expires_at"] = datetime.now(
        timezone.utc
    ) - timedelta(seconds=1)
    assert await task_store.reap_expired_leases() == {"failed": 0, "requeued": 1}
    resumed = await task_store.claim_task(
        replacement.id,
        "org-a",
        [],
        "default",
        replacement.capabilities.routing_labels(),
        project_id="project-a",
    )
    assert resumed is not None
    assert resumed["run_id"] == run_id
    assert resumed["payload"] == claimed["payload"]
    expected = _accepted_review(run_id)
    await task_store.report_task(
        run_id,
        "completed",
        expected.model_dump_json(),
        None,
        worker_id=replacement.id,
    )

    record = await asyncio.wait_for(started, timeout=1)
    assert record.status == "READY_TO_MERGE"
    assert analyzer.calls == 1
    assert implementer.calls == 1
    assert repairer.calls == 0
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
    assert OperatorResult.model_validate_json(persisted["output"]) == expected
