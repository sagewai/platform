# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Primary-interface recovery stays bound to the accepted Fleet route."""

from __future__ import annotations

import asyncio
import json
import subprocess
from importlib import import_module

import pytest
from click.testing import CliRunner

from sagewai.cli import cli
from sagewai.db.engine import create_engine
from sagewai.db.models import Base
from sagewai.fleet import FleetDispatcher, PostgresFleetRegistry
from sagewai.fleet.task_store import PostgresTaskStore
from sagewai.work import WorkEventType, WorkStore
from sagewai.work.profiles.software.fleet_worker import (
    SoftwareFleetTaskHandler,
    SoftwareFleetWorkspaceResolver,
)
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager
from tests.work.test_fleet_lifecycle import (
    _clone_worker_repository,
    _register_worker,
    _worker_runner,
)
from tests.work.test_fleet_primary_interface import (
    _LocalVerification,
    _persisted_task_payloads,
    _pump_workers_until_done,
    _WorkerClaudeRuntime,
    _WorkerCodexRuntime,
)
from tests.work.test_lifecycle import _repository

work_module = import_module("sagewai.cli.work")


@pytest.mark.asyncio
async def test_work_cli_resume_requires_the_original_fleet_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'fleet-recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repository, _ = _repository(tmp_path)
    (repository / "justfile").write_text("smoke:\n    @true\n")
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
    claude_runtime = _WorkerClaudeRuntime(local_auth="claude-worker-local-auth")
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
            claude_runtime=claude_runtime,
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
    monkeypatch.setattr(work_module, "SandboxedVerificationRunner", _LocalVerification)

    async def repository_state():
        return repository, base_sha

    monkeypatch.setattr(work_module, "_repository_state", repository_state)
    normal_run_async = work_module._cli._run_async

    def interrupt_after_first_task(coroutine):
        async def run_and_interrupt():
            execution = asyncio.create_task(coroutine)
            for _ in range(2000):
                tasks = await task_store.list_tasks(org_id="org-a", project_id="project-a")
                if len(tasks) == 2 and sum(task["status"] == "completed" for task in tasks) == 1:
                    work_id = tasks[0]["run_id"].split(":", 1)[0]
                    events = await WorkStore(engine=engine).read_events(
                        work_id,
                        project_id="project-a",
                    )
                    started = [
                        event for event in events if event.event_type is WorkEventType.STAGE_STARTED
                    ]
                    if len(started) == 2:
                        execution.cancel()
                        await asyncio.gather(execution, return_exceptions=True)
                        raise RuntimeError("simulated process interruption")
                await asyncio.sleep(0.001)
            raise AssertionError("second Fleet stage was not durably started")

        return asyncio.run(run_and_interrupt())

    monkeypatch.setattr(work_module._cli, "_run_async", interrupt_after_first_task)
    started = asyncio.create_task(
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
                "Change target through Fleet with recovery",
            ],
        )
    )
    initial_pump = asyncio.create_task(_pump_workers_until_done(started, claude_runner))
    try:
        interrupted = await asyncio.wait_for(started, timeout=20)
        first_worker_results = await asyncio.wait_for(initial_pump, timeout=2)
    finally:
        if not initial_pump.done():
            initial_pump.cancel()
            await asyncio.gather(initial_pump, return_exceptions=True)
    monkeypatch.setattr(work_module._cli, "_run_async", normal_run_async)

    assert interrupted.exit_code == 1
    assert isinstance(interrupted.exception, RuntimeError)
    assert str(interrupted.exception) == "simulated process interruption"
    first_claim = next(item for item in first_worker_results if item.get("claimed"))
    work_id = first_claim["run_id"].split(":", 1)[0]
    tasks_at_interruption = await task_store.list_tasks(org_id="org-a", project_id="project-a")
    assert {
        task["run_id"].rsplit(":", 2)[-2]: task["status"] for task in tasks_at_interruption
    } == {"analysis": "completed", "implement": "pending"}

    local_resume = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        ["work", "--project", "project-a", "resume", work_id],
    )
    wrong_org_resume = await asyncio.to_thread(
        CliRunner().invoke,
        cli,
        [
            "work",
            "--project",
            "project-a",
            "--execution",
            "fleet",
            "--fleet-org",
            "org-b",
            "resume",
            work_id,
        ],
    )
    assert local_resume.exit_code == 1
    assert "unfinished operator runtime changed" in local_resume.output
    assert wrong_org_resume.exit_code == 1
    assert "unfinished operator runtime changed" in wrong_org_resume.output
    assert (
        await task_store.list_tasks(org_id="org-a", project_id="project-a") == tasks_at_interruption
    )
    interrupted_events = await WorkStore(engine=engine).read_events(
        work_id,
        project_id="project-a",
    )
    assert sum(event.event_type is WorkEventType.STAGE_STARTED for event in interrupted_events) == 2

    resumed = asyncio.create_task(
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
                "resume",
                work_id,
            ],
        )
    )
    resumed_pump = asyncio.create_task(
        _pump_workers_until_done(resumed, claude_runner, codex_runner)
    )
    try:
        result = await asyncio.wait_for(resumed, timeout=20)
        resumed_worker_results = await asyncio.wait_for(resumed_pump, timeout=2)
    finally:
        if not resumed_pump.done():
            resumed_pump.cancel()
            await asyncio.gather(resumed_pump, return_exceptions=True)
        await claude_runner.http_client.aclose()
        await codex_runner.http_client.aclose()

    assert result.exit_code == 0, result.output
    assert result.output == f"Work {work_id}: COMPLETE\n"
    persisted = await WorkStore(engine=engine).load_work(
        work_id,
        project_id="project-a",
    )
    assert persisted is not None and persisted.status == "COMPLETE"
    tasks = await task_store.list_tasks(org_id="org-a", project_id="project-a")
    assert len(tasks) == 3
    assert {task["run_id"].rsplit(":", 2)[-2]: task["worker_id"] for task in tasks} == {
        "analysis": claude_worker.id,
        "implement": codex_worker.id,
        "review": claude_worker.id,
    }
    events = await WorkStore(engine=engine).read_events(
        work_id,
        project_id="project-a",
    )
    started_by_stage = [
        event.payload_json["stage"]
        for event in events
        if event.event_type is WorkEventType.STAGE_STARTED
    ]
    assert started_by_stage == ["analysis", "implement", "implement", "review"]
    serialized = json.dumps(await _persisted_task_payloads(engine))
    assert claude_runtime.local_auth not in serialized
    assert codex_runtime.local_auth not in serialized
    assert claude_runtime.calls == ["analysis", "review"]
    assert codex_runtime.calls == ["implement"]
    assert sum(item.get("claimed", False) for item in resumed_worker_results) == 2

    await engine.dispose()
