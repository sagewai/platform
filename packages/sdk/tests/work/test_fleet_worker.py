# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker-side execution of typed Work operator Fleet tasks."""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

import pytest

from sagewai.fleet.runner import WorkerRunner, WorkerTaskContext
from sagewai.work.fleet import (
    FleetOperatorResultEnvelope,
    FleetWorkspaceTransfer,
    FleetWorkspaceTransferResult,
)
from sagewai.work.profiles.software.fleet_worker import (
    SoftwareFleetTaskHandler,
    SoftwareFleetWorkspaceResolver,
)
from sagewai.work.profiles.software.fleet_workspace import (
    SOFTWARE_FLEET_WORKSPACE_KIND,
    SoftwareFleetWorkspaceInput,
    SoftwareFleetWorkspaceOutput,
    SoftwareFleetWorkspaceTransport,
    software_repository_ref,
)
from sagewai.work.profiles.software.models import SoftwareWorkspace
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager, workspace_diff
from sagewai.work.runtime import ClaudeRuntime, CodexRuntime

from .test_fleet_runtime import _capabilities, _capsule, _request, _result


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(repository: Path, *args: str, input_value: bytes | None = None) -> bytes:
    return subprocess.run(
        ("git", *args),
        cwd=repository,
        input=input_value,
        capture_output=True,
        check=True,
    ).stdout


def _repositories(tmp_path: Path) -> tuple[Path, Path, str]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "config", "user.email", "fleet@example.test")
    _git(seed, "config", "user.name", "Fleet Test")
    (seed / "target.bin").write_bytes(b"base\x00state\n")
    (seed / "target.txt").write_text("base state\n")
    _git(seed, "add", "target.bin", "target.txt")
    _git(seed, "commit", "-m", "base")
    base_sha = _git(seed, "rev-parse", "HEAD").decode().strip()

    central = tmp_path / "central"
    worker = tmp_path / "worker"
    _git(tmp_path, "clone", str(seed), str(central))
    _git(tmp_path, "clone", str(seed), str(worker))
    origin = "https://github.com/sagewai/platform.git"
    _git(central, "remote", "set-url", "origin", origin)
    _git(worker, "remote", "set-url", "origin", origin)
    return central, worker, base_sha


def _snapshot() -> FleetWorkspaceTransfer:
    diff = b"diff --git a/a.py b/a.py\n"
    payload = SoftwareFleetWorkspaceInput(
        repository_ref=f"git-origin://sha256:{'1' * 64}",
        base_sha="a" * 40,
        current_sha="b" * 40,
        cumulative_diff_base64=base64.b64encode(diff).decode("ascii"),
    )
    return FleetWorkspaceTransfer(
        ref="workspace://attempt-1",
        project_id="project-a",
        work_id="work-1",
        kind=SOFTWARE_FLEET_WORKSPACE_KIND,
        input_digest=_digest(diff),
        payload=payload.model_dump(mode="json"),
    )


def _software_transfer(
    *,
    repository_ref: str,
    base_sha: str,
    current_sha: str,
    cumulative_diff: bytes,
    ref: str = "workspace://run-1",
    work_id: str = "work-1",
) -> FleetWorkspaceTransfer:
    payload = SoftwareFleetWorkspaceInput(
        repository_ref=repository_ref,
        base_sha=base_sha,
        current_sha=current_sha,
        cumulative_diff_base64=base64.b64encode(cumulative_diff).decode("ascii"),
    )
    return FleetWorkspaceTransfer(
        ref=ref,
        project_id="project-a",
        work_id=work_id,
        kind=SOFTWARE_FLEET_WORKSPACE_KIND,
        input_digest=_digest(cumulative_diff),
        payload=payload.model_dump(mode="json"),
    )


def _task(*, runtime: str = "runtime.claude") -> dict:
    request = _request()
    capabilities = _capabilities().model_dump(mode="json")
    capabilities["grants"][0]["credential_ref"] = None
    return {
        "run_id": request.run_id,
        "project_id": request.project_id,
        "payload": {
            "kind": "work.operator",
            "request": request.model_dump(mode="json"),
            "capsule": _capsule().model_dump(mode="json"),
            "capabilities": capabilities,
            "required_capabilities": [runtime, "cli.git"],
            "workspace": _snapshot().model_dump(mode="json"),
        },
    }


class _Workspace:
    ref = "workspace://attempt-1"
    project_id = "project-a"
    work_id = "work-1"
    path = Path("/worker-owned/platform")


class _Resolver:
    def __init__(self, *, delta: bytes = b"") -> None:
        self.materialized: list[FleetWorkspaceTransfer] = []
        self.captured: list[object] = []
        self.delta = delta

    async def materialize(self, snapshot: FleetWorkspaceTransfer) -> _Workspace:
        self.materialized.append(snapshot)
        return _Workspace()

    async def capture(
        self,
        snapshot: FleetWorkspaceTransfer,
        workspace: object,
    ) -> FleetWorkspaceTransferResult:
        self.captured.append(workspace)
        input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
        result_diff = (
            base64.b64decode(input_payload.cumulative_diff_base64) + self.delta
        )
        output_payload = SoftwareFleetWorkspaceOutput(
            repository_ref=input_payload.repository_ref,
            base_sha=input_payload.base_sha,
            current_sha=input_payload.current_sha,
            delta_diff_base64=base64.b64encode(self.delta).decode("ascii"),
            delta_diff_sha256=_digest(self.delta),
        )
        return FleetWorkspaceTransferResult(
            ref=snapshot.ref,
            project_id=snapshot.project_id,
            work_id=snapshot.work_id,
            kind=snapshot.kind,
            input_digest=snapshot.input_digest,
            result_digest=_digest(result_diff),
            payload=output_payload.model_dump(mode="json"),
        )


class _Claude(ClaudeRuntime):
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls.append((request, capsule, capabilities, workspace))
        return _result()


class _Codex(CodexRuntime):
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def run(self, request, capsule, capabilities, workspace):
        self.calls.append((request, capsule, capabilities, workspace))
        return _result()


@pytest.mark.asyncio
async def test_worker_runner_fails_closed_for_work_task_without_handler() -> None:
    runner = WorkerRunner(base_url="http://test", exec_cmd="printf should-not-run")

    status, output, error = await runner._execute(_task())

    assert status == "failed"
    assert output is None
    assert error == "work.operator task handler is not configured"


@pytest.mark.asyncio
async def test_worker_runner_executes_work_task_only_through_injected_handler() -> None:
    contexts: list[WorkerTaskContext] = []

    async def handler(task: dict, context: WorkerTaskContext) -> str:
        contexts.append(context)
        return "typed-result"

    runner = WorkerRunner(
        base_url="http://test",
        project="project-a",
        capability_names=["runtime.claude", "cli.git"],
        task_handler=handler,
        exec_cmd="printf should-not-run",
    )

    status, output, error = await runner._execute(_task())

    assert (status, output, error) == ("completed", "typed-result", None)
    assert contexts == [
        WorkerTaskContext(
            project_id="project-a",
            capability_names=("runtime.claude", "cli.git"),
        )
    ]


@pytest.mark.asyncio
async def test_worker_runner_does_not_report_or_log_handler_exception_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(task: dict, context: WorkerTaskContext) -> str:
        raise ValueError("central-secret-value")

    runner = WorkerRunner(
        base_url="http://test",
        project="project-a",
        capability_names=["runtime.claude", "cli.git"],
        task_handler=handler,
    )

    status, output, error = await runner._execute(_task())

    assert (status, output, error) == (
        "failed",
        None,
        "work.operator task execution failed",
    )
    assert "central-secret-value" not in caplog.text


@pytest.mark.asyncio
async def test_software_handler_runs_only_advertised_native_runtime() -> None:
    resolver = _Resolver()
    claude = _Claude()
    codex = _Codex()
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=resolver,
        claude_runtime=claude,
        codex_runtime=codex,
    )

    raw = await handler(
        _task(),
        WorkerTaskContext(
            project_id="project-a",
            capability_names=("runtime.claude", "cli.git"),
        ),
    )

    output = FleetOperatorResultEnvelope.model_validate_json(raw)
    assert output.kind == "work.operator.result"
    assert output.result == _result()
    assert output.workspace_result is not None
    workspace_output = SoftwareFleetWorkspaceOutput.model_validate(
        output.workspace_result.payload
    )
    snapshot_input = SoftwareFleetWorkspaceInput.model_validate(_snapshot().payload)
    assert workspace_output.current_sha == snapshot_input.current_sha
    assert len(claude.calls) == 1
    assert codex.calls == []
    assert resolver.materialized == [_snapshot()]
    assert resolver.captured == [claude.calls[0][3]]


@pytest.mark.asyncio
async def test_software_handler_maps_codex_capability_only_to_codex_runtime() -> None:
    claude = _Claude()
    codex = _Codex()
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=_Resolver(),
        claude_runtime=claude,
        codex_runtime=codex,
    )

    await handler(
        _task(runtime="runtime.codex"),
        WorkerTaskContext(
            project_id="project-a",
            capability_names=("runtime.codex", "cli.git"),
        ),
    )

    assert len(codex.calls) == 1
    assert claude.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda task: task.update(project_id="project-b"),
            "top-level project does not match request",
        ),
        (
            lambda task: task.update(run_id="run-other"),
            "top-level run does not match request",
        ),
        (
            lambda task: task["payload"]["capsule"].update(work_id="work-other"),
            "capsule",
        ),
        (
            lambda task: task["payload"].update(
                required_capabilities=["runtime.claude"]
            ),
            "required capabilities do not match",
        ),
    ],
)
async def test_software_handler_rejects_mismatched_task_identity(mutate, match) -> None:
    task = _task()
    mutate(task)
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=_Resolver(),
        claude_runtime=_Claude(),
        codex_runtime=_Codex(),
    )

    with pytest.raises(ValueError, match=match):
        await handler(
            task,
            WorkerTaskContext(
                project_id="project-a",
                capability_names=("runtime.claude", "cli.git"),
            ),
        )


@pytest.mark.asyncio
async def test_software_handler_rejects_unadvertised_or_ambiguous_runtime() -> None:
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=_Resolver(),
        claude_runtime=_Claude(),
        codex_runtime=_Codex(),
    )
    context = WorkerTaskContext(
        project_id="project-a",
        capability_names=("runtime.codex", "cli.git"),
    )

    with pytest.raises(ValueError, match="worker did not advertise"):
        await handler(_task(), context)

    task = _task()
    task["payload"]["required_capabilities"].insert(1, "runtime.codex")
    with pytest.raises(ValueError, match="exactly one supported runtime"):
        await handler(
            task,
            WorkerTaskContext(
                project_id="project-a",
                capability_names=("runtime.claude", "runtime.codex", "cli.git"),
            ),
        )


@pytest.mark.asyncio
async def test_software_handler_rejects_workspace_delta_without_write_capability() -> None:
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=_Resolver(delta=b"unexpected mutation"),
        claude_runtime=_Claude(),
        codex_runtime=_Codex(),
    )

    with pytest.raises(ValueError, match="read-only operator changed the workspace"):
        await handler(
            _task(),
            WorkerTaskContext(
                project_id="project-a",
                capability_names=("runtime.claude", "cli.git"),
            ),
        )


@pytest.mark.asyncio
async def test_software_handler_rejects_central_credential_reference() -> None:
    task = _task()
    task["payload"]["capabilities"]["grants"][0]["credential_ref"] = (
        "credential://central/git"
    )
    handler = SoftwareFleetTaskHandler(
        workspace_resolver=_Resolver(),
        claude_runtime=_Claude(),
        codex_runtime=_Codex(),
    )

    with pytest.raises(ValueError, match="credential"):
        await handler(
            task,
            WorkerTaskContext(
                project_id="project-a",
                capability_names=("runtime.claude", "cli.git"),
            ),
        )


def test_software_workspace_input_rejects_invalid_binary_diff() -> None:
    values = SoftwareFleetWorkspaceInput.model_validate(
        _snapshot().payload
    ).model_dump(mode="json")
    values["cumulative_diff_base64"] = "not-base64"

    with pytest.raises(ValueError, match="cumulative diff is not valid base64"):
        SoftwareFleetWorkspaceInput.model_validate(values)


@pytest.mark.asyncio
async def test_concrete_resolver_round_trips_binary_workspace_delta(tmp_path: Path) -> None:
    central, worker, base_sha = _repositories(tmp_path)
    (central / "target.bin").write_bytes(b"input\x00state\n")
    starting_diff = _git(
        central,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        base_sha,
        "--",
    )
    snapshot = _software_transfer(
        repository_ref=await software_repository_ref(worker),
        base_sha=base_sha,
        current_sha=base_sha,
        cumulative_diff=starting_diff,
    )
    resolver = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worker-worktrees"),
    )

    workspace = await resolver.materialize(snapshot)
    assert workspace.repository == worker.resolve()
    assert workspace.path != central
    assert (workspace.path / "target.bin").read_bytes() == b"input\x00state\n"

    (workspace.path / "target.bin").write_bytes(b"output\x00state\n")
    result = await resolver.capture(snapshot, workspace)
    output_payload = SoftwareFleetWorkspaceOutput.model_validate(result.payload)
    delta = base64.b64decode(output_payload.delta_diff_base64, validate=True)
    _git(
        central,
        "apply",
        "--binary",
        "--whitespace=nowarn",
        "--",
        input_value=delta,
    )

    assert (central / "target.bin").read_bytes() == b"output\x00state\n"
    resulting_diff = _git(
        central,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        base_sha,
        "--",
    )
    assert result.input_digest == snapshot.input_digest
    assert result.result_digest == _digest(resulting_diff)
    assert output_payload.delta_diff_sha256 == _digest(delta)


@pytest.mark.asyncio
async def test_concrete_resolver_round_trips_text_delta_across_core_abbrev(
    tmp_path: Path,
) -> None:
    central, worker, base_sha = _repositories(tmp_path)
    coordinator_abbrev = "7"
    worker_abbrev = "12"
    assert len(base_sha) not in {int(coordinator_abbrev), int(worker_abbrev)}
    _git(central, "config", "core.abbrev", coordinator_abbrev)
    _git(worker, "config", "core.abbrev", worker_abbrev)
    repository_ref = await software_repository_ref(worker)
    transport = SoftwareFleetWorkspaceTransport(repository_ref=repository_ref)
    central_workspace = SoftwareWorkspace(
        ref="workspace://run-1",
        project_id="project-a",
        work_id="work-1",
        attempt_id="run-1",
        repository=central,
        path=central,
        base_sha=base_sha,
        initial_sha=base_sha,
    )
    (central / "target.txt").write_text("coordinator input\n")

    snapshot = await transport.snapshot(central_workspace)
    resolver = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worker-worktrees"),
    )
    workspace = await resolver.materialize(snapshot)
    assert (workspace.path / "target.txt").read_text() == "coordinator input\n"

    (workspace.path / "target.txt").write_text("worker output\n")
    result = await resolver.capture(snapshot, workspace)
    await transport.apply(central_workspace, snapshot, result)

    assert (central / "target.txt").read_text() == "worker output\n"
    input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
    applied_diff, _ = await workspace_diff(
        central_workspace.model_copy(update={"base_sha": input_payload.current_sha})
    )
    assert _digest(applied_diff.encode()) == result.result_digest


@pytest.mark.asyncio
async def test_concrete_resolver_isolates_overlapping_works_with_same_ref(
    tmp_path: Path,
) -> None:
    _, worker, base_sha = _repositories(tmp_path)
    repository_ref = await software_repository_ref(worker)
    first_snapshot = _software_transfer(
        repository_ref=repository_ref,
        base_sha=base_sha,
        current_sha=base_sha,
        cumulative_diff=b"",
        work_id="work-1",
    )
    second_snapshot = _software_transfer(
        repository_ref=repository_ref,
        base_sha=base_sha,
        current_sha=base_sha,
        cumulative_diff=b"",
        work_id="work-2",
    )
    resolver = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worker-worktrees"),
    )

    first_workspace = await resolver.materialize(first_snapshot)
    second_workspace = await resolver.materialize(second_snapshot)
    (first_workspace.path / "first.txt").write_text("first work")
    (second_workspace.path / "second.txt").write_text("second work")

    second_result = await resolver.capture(second_snapshot, second_workspace)
    first_result = await resolver.capture(first_snapshot, first_workspace)

    assert second_result.work_id == "work-2"
    assert first_result.work_id == "work-1"
    assert first_result.result_digest != second_result.result_digest


@pytest.mark.asyncio
async def test_concrete_resolver_rejects_task_repository_mismatch(tmp_path: Path) -> None:
    central, worker, base_sha = _repositories(tmp_path)
    snapshot = _software_transfer(
        repository_ref=f"git-origin://sha256:{'0' * 64}",
        base_sha=base_sha,
        current_sha=base_sha,
        cumulative_diff=b"",
    )
    resolver = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worker-worktrees"),
    )

    with pytest.raises(ValueError, match="does not match configured repository"):
        await resolver.materialize(snapshot)

    assert central.exists()
    assert not (tmp_path / "worker-worktrees").exists()


@pytest.mark.asyncio
async def test_concrete_resolver_retry_cleans_partial_worker_state(tmp_path: Path) -> None:
    central, worker, base_sha = _repositories(tmp_path)
    (central / "target.bin").write_bytes(b"canonical\x00input\n")
    starting_diff = _git(
        central,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        base_sha,
        "--",
    )
    snapshot = _software_transfer(
        repository_ref=await software_repository_ref(worker),
        base_sha=base_sha,
        current_sha=base_sha,
        cumulative_diff=starting_diff,
    )
    worktree_root = tmp_path / "worker-worktrees"
    first = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=worktree_root),
    )
    interrupted = await first.materialize(snapshot)
    (interrupted.path / "target.bin").write_bytes(b"partial operator mutation")
    (interrupted.path / "stale.tmp").write_text("partial untracked mutation")

    restarted = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=worktree_root),
    )
    restored = await restarted.materialize(snapshot)

    assert (restored.path / "target.bin").read_bytes() == b"canonical\x00input\n"
    assert not (restored.path / "stale.tmp").exists()


@pytest.mark.asyncio
async def test_concrete_resolver_applies_diff_relative_to_current_sha(tmp_path: Path) -> None:
    central, worker, base_sha = _repositories(tmp_path)
    _git(worker, "config", "user.email", "fleet@example.test")
    _git(worker, "config", "user.name", "Fleet Test")
    (worker / "target.bin").write_bytes(b"committed\x00state\n")
    _git(worker, "add", "target.bin")
    _git(worker, "commit", "-m", "new current")
    current_sha = _git(worker, "rev-parse", "HEAD").decode().strip()
    _git(central, "fetch", str(worker), current_sha)
    _git(central, "reset", "--hard", current_sha)
    (central / "target.bin").write_bytes(b"uncommitted\x00input\n")
    starting_diff = _git(
        central,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        current_sha,
        "--",
    )
    snapshot = _software_transfer(
        repository_ref=await software_repository_ref(worker),
        base_sha=base_sha,
        current_sha=current_sha,
        cumulative_diff=starting_diff,
    )
    resolver = SoftwareFleetWorkspaceResolver(
        repository=worker,
        worktree_manager=SoftwareWorktreeManager(root=tmp_path / "worker-worktrees"),
    )

    workspace = await resolver.materialize(snapshot)

    assert workspace.base_sha == current_sha
    assert (workspace.path / "target.bin").read_bytes() == b"uncommitted\x00input\n"
