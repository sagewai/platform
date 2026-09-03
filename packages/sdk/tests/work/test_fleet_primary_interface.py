# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Primary-interface proof for capability-matched Fleet Work execution."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from importlib import import_module

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from sagewai.cli import cli
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, FleetTaskModel
from sagewai.fleet import FleetDispatcher, PostgresFleetRegistry
from sagewai.fleet.execution import WorkerProcessResult
from sagewai.fleet.task_store import PostgresTaskStore
from sagewai.work import (
    OperatorResult,
    ProposedAcceptanceCriterion,
    ReviewResult,
    WorkAnalysisResult,
    WorkContractProposal,
    WorkEventType,
    WorkStore,
)
from sagewai.work.profiles.software.fleet_worker import (
    SoftwareFleetTaskHandler,
    SoftwareFleetWorkspaceResolver,
)
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from sagewai.work.runtime import ClaudeRuntime, CodexRuntime
from tests.work.test_fleet_lifecycle import (
    _clone_worker_repository,
    _register_worker,
    _worker_runner,
)
from tests.work.test_lifecycle import _operator_result, _repository


async def _persisted_task_payloads(engine) -> list[dict]:
    async with engine.connect() as connection:
        tasks = (await connection.execute(select(FleetTaskModel.__table__))).all()
    return [task._mapping["payload"] for task in tasks]


async def _pump_workers_until_done(invocation, *runners) -> list[dict]:
    results: list[dict] = []
    while not invocation.done():
        results.extend(await asyncio.gather(*(runner.run_once() for runner in runners)))
        await asyncio.sleep(0.001)
    return results


work_module = import_module("sagewai.cli.work")
assembly_module = import_module("sagewai.work.profiles.software.assembly")


class _LocalVerification:
    def __init__(self, *, image: str) -> None:
        self.image = image

    async def run(self, *, project_id, work_id, attempt_id, workspace, commands, timeout):
        del project_id, work_id, attempt_id, workspace, timeout
        assert commands == (("just", "smoke"),)
        return tuple(
            WorkerProcessResult(returncode=0, stdout="passed", stderr="") for _ in commands
        )


class _WorkerClaudeRuntime(ClaudeRuntime):
    def __init__(self, *, local_auth: str) -> None:
        self.local_auth = local_auth
        self.calls: list[str] = []

    async def run(self, request, capsule, capabilities, workspace) -> OperatorResult:
        self.calls.append(request.stage)
        if request.stage == "analysis":
            analysis = WorkAnalysisResult(
                attempt_id=request.run_id,
                proposal=WorkContractProposal(
                    goal="Change target through capability-matched Fleet workers",
                    allowed_scope=("target.txt",),
                    acceptance_criteria=(
                        ProposedAcceptanceCriterion(
                            statement="deterministic verification passes",
                            verification_kind="deterministic",
                        ),
                    ),
                    constraints=(),
                    non_goals=(),
                    risk="low",
                    design_required=False,
                ),
                claims=(),
            )
            return _operator_result(
                request,
                profile_context={"analysis_result": analysis.model_dump(mode="json")},
            )
        if request.stage == "review":
            review = ReviewResult(
                project_id=request.project_id,
                attempt_id=request.run_id,
                verdict="accept",
                findings=(),
                evidence_refs=(f"worker://claude/{request.run_id}",),
                introduced_assumptions=(),
                unsupported_claims=(),
                scope_expansions=(),
                unsupported_implementation_choices=(),
            )
            return _operator_result(
                request,
                profile_context={"review_result": review.model_dump(mode="json")},
            )
        raise AssertionError(f"Claude worker received unexpected stage {request.stage}")


class _WorkerCodexRuntime(CodexRuntime):
    def __init__(self, *, local_auth: str) -> None:
        self.local_auth = local_auth
        self.calls: list[str] = []

    async def run(self, request, capsule, capabilities, workspace) -> OperatorResult:
        self.calls.append(request.stage)
        if request.stage != "implement":
            raise AssertionError(f"Codex worker received unexpected stage {request.stage}")
        (workspace.path / "target.txt").write_text("implemented\n")
        return _operator_result(request)


@pytest.mark.asyncio
async def test_work_cli_completes_through_heterogeneous_fleet_workers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'fleet-primary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository, _ = _repository(tmp_path)
    (repository / "justfile").write_text('smoke:\n    @test "$(cat target.txt)" = "implemented"\n')
    subprocess.run(("git", "-C", str(repository), "add", "justfile"), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "commit", "-qm", "add smoke check"),
        check=True,
    )
    base_sha = subprocess.check_output(
        ("git", "-C", str(repository), "rev-parse", "HEAD"), text=True
    ).strip()
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

    registry = PostgresFleetRegistry(engine=engine)
    task_store = PostgresTaskStore(engine=engine)
    await registry.init()
    await task_store.init()
    claude_worker = await _register_worker(
        registry,
        name="claude-read-worker",
        project_id="project-a",
        capabilities=("runtime.claude", "filesystem.read"),
    )
    codex_worker = await _register_worker(
        registry,
        name="codex-write-worker",
        project_id="project-a",
        capabilities=("runtime.codex", "filesystem.write"),
    )
    claude_analysis_runtime = _WorkerClaudeRuntime(
        local_auth="claude-worker-local-auth"
    )
    claude_review_runtime = _WorkerClaudeRuntime(
        local_auth="claude-worker-local-auth"
    )
    codex_runtime = _WorkerCodexRuntime(local_auth="codex-worker-local-auth")
    dispatcher = FleetDispatcher(task_store, poll_interval=0.001, poll_timeout=0.01)
    claude_runner = _worker_runner(
        worker=claude_worker,
        registry=registry,
        dispatcher=dispatcher,
        task_store=task_store,
        task_handler=SoftwareFleetTaskHandler(
            workspace_resolver=SoftwareFleetWorkspaceResolver(
                repository=_clone_worker_repository(
                    repository,
                    tmp_path / "claude-worker-repository",
                ),
                worktree_manager=SoftwareWorktreeManager(root=tmp_path / "claude-worker-worktrees"),
            ),
            claude_analysis_runtime=claude_analysis_runtime,
            claude_review_runtime=claude_review_runtime,
        ),
    )
    codex_runner = _worker_runner(
        worker=codex_worker,
        registry=registry,
        dispatcher=dispatcher,
        task_store=task_store,
        task_handler=SoftwareFleetTaskHandler(
            workspace_resolver=SoftwareFleetWorkspaceResolver(
                repository=_clone_worker_repository(
                    repository,
                    tmp_path / "codex-worker-repository",
                ),
                worktree_manager=SoftwareWorktreeManager(root=tmp_path / "codex-worker-worktrees"),
            ),
            codex_runtime=codex_runtime,
        ),
    )

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "SAGEWAI_WORK_VERIFICATION_IMAGE",
        "example.invalid/verifier@sha256:" + "a" * 64,
    )
    monkeypatch.setattr(work_module.factory, "get_engine", lambda: engine)
    monkeypatch.setattr(assembly_module, "SandboxedVerificationRunner", _LocalVerification)

    async def repository_state():
        return repository, base_sha

    monkeypatch.setattr(work_module, "_repository_state", repository_state)

    invocation = asyncio.create_task(
        asyncio.to_thread(
            CliRunner().invoke,
            cli,
            [
                "work",
                "--project",
                "project-a",
                "--execution",
                "fleet",
                "--fleet-org",
                "org-a",
                "start",
                "Change target through Fleet",
            ],
        )
    )
    pump = asyncio.create_task(_pump_workers_until_done(invocation, claude_runner, codex_runner))
    try:
        result = await asyncio.wait_for(invocation, timeout=20)
        worker_results = await asyncio.wait_for(pump, timeout=2)
    finally:
        if not pump.done():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        await claude_runner.http_client.aclose()
        await codex_runner.http_client.aclose()

    if result.exception is not None:
        raise result.exception
    assert result.exit_code == 0, result.output
    match = re.fullmatch(r"Work ([0-9a-f-]+): COMPLETE\n", result.output)
    assert match is not None
    work_id = match.group(1)

    persisted = await WorkStore(engine=engine).load_work(
        work_id,
        project_id="project-a",
    )
    assert persisted is not None
    assert persisted.status == "COMPLETE"
    events = await WorkStore(engine=engine).read_events(
        work_id,
        project_id="project-a",
    )
    assert {
        event.payload_json.get("stage")
        for event in events
        if event.event_type is WorkEventType.STAGE_COMPLETED
    } >= {"analysis", "implement"}
    assert any(event.event_type is WorkEventType.REVIEW_RECORDED for event in events)

    tasks = await task_store.list_tasks(org_id="org-a", project_id="project-a")
    assert len(tasks) == 3
    selected_by_stage = {task["run_id"].rsplit(":", 2)[-2]: task["worker_id"] for task in tasks}
    assert selected_by_stage == {
        "analysis": claude_worker.id,
        "implement": codex_worker.id,
        "review": claude_worker.id,
    }
    assert claude_analysis_runtime.calls == ["analysis"]
    assert claude_review_runtime.calls == ["review"]
    assert codex_runtime.calls == ["implement"]
    assert {item["run_id"] for item in worker_results if item.get("claimed")} == {
        task["run_id"] for task in tasks
    }

    payloads = await _persisted_task_payloads(engine)
    serialized_payloads = json.dumps(payloads)
    assert claude_analysis_runtime.local_auth not in serialized_payloads
    assert claude_review_runtime.local_auth not in serialized_payloads
    assert codex_runtime.local_auth not in serialized_payloads
    assert all(
        grant["credential_ref"] is None
        for payload in payloads
        for grant in payload["capabilities"]["grants"]
    )

    await engine.dispose()
