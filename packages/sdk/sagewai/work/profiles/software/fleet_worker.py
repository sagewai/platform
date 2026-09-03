# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Worker-local execution for software Work tasks claimed through Fleet."""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sagewai.engines.universal import UniversalAgent
from sagewai.fleet.execution import run_worker_subprocess
from sagewai.fleet.runner import WorkerTaskContext
from sagewai.work.activity import (
    FLEET_ACTIVITY_LOG_MAX_BYTES,
    ActivitySink,
    OperatorActivity,
    bounded_ndjson,
)
from sagewai.work.activity_ingestion import BatchingActivitySink
from sagewai.work.fleet import (
    FleetOperatorResultEnvelope,
    FleetOperatorTaskPayload,
    FleetWorkspaceTransfer,
    FleetWorkspaceTransferResult,
)
from sagewai.work.harness_tools import McpConnectionResolver
from sagewai.work.profiles.software.fleet_workspace import (
    SOFTWARE_FLEET_WORKSPACE_KIND,
    SoftwareFleetWorkspaceInput,
    SoftwareFleetWorkspaceOutput,
    software_repository_ref,
)
from sagewai.work.profiles.software.models import SoftwareWorkspace, WorkspaceStaleError
from sagewai.work.profiles.software.scm import SoftwareWorktreeManager, workspace_diff
from sagewai.work.runtime import (
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    OperatorResult,
    OperatorRuntime,
    WorkRequest,
    Workspace,
)
from sagewai.work.runtime_harness import HarnessRuntime
from sagewai.work.tasks.models import HarnessTier


class FleetWorkerWorkspaceResolver(Protocol):
    """Materialize a trusted worker-local workspace and capture its result."""

    async def materialize(self, snapshot: FleetWorkspaceTransfer) -> Workspace: ...

    async def capture(
        self,
        snapshot: FleetWorkspaceTransfer,
        workspace: Workspace,
    ) -> FleetWorkspaceTransferResult: ...


@dataclass(frozen=True)
class _MaterializedState:
    path: Path
    input_tree: str


class _ActivityFanoutSink:
    def __init__(
        self,
        *,
        log: list[str],
        progress_sink: ActivitySink,
    ) -> None:
        self._log = log
        self._progress_sink = progress_sink
        self._log_bytes = 0

    def emit(self, activity: OperatorActivity) -> None:
        if self._log_bytes <= FLEET_ACTIVITY_LOG_MAX_BYTES:
            line = activity.model_dump_json()
            self._log.append(line)
            self._log_bytes += len(line.encode("utf-8")) + 1
        self._progress_sink.emit(activity)


class SoftwareFleetWorkspaceResolver:
    """Materialize tasks only from one explicitly configured local repository."""

    def __init__(
        self,
        *,
        repository: Path,
        worktree_manager: SoftwareWorktreeManager | None = None,
    ) -> None:
        self._repository = repository.resolve()
        self._worktrees = worktree_manager or SoftwareWorktreeManager()
        self._materialized: dict[tuple[str, str, str], _MaterializedState] = {}

    async def materialize(self, snapshot: FleetWorkspaceTransfer) -> SoftwareWorkspace:
        input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
        if snapshot.kind != SOFTWARE_FLEET_WORKSPACE_KIND:
            raise ValueError("Fleet workspace transfer is not software Git state")
        if snapshot.project_id is None:
            raise ValueError("software Fleet workspaces require a project")
        if not self._repository.is_dir():
            raise WorkspaceStaleError("configured worker repository is unavailable")
        if input_payload.repository_ref != await software_repository_ref(self._repository):
            raise ValueError("Fleet task repository does not match configured repository")
        attempt_id = self._attempt_id(snapshot.ref)
        workspace_key = (snapshot.project_id, snapshot.work_id, snapshot.ref)
        if workspace_key in self._materialized:
            raise WorkspaceStaleError("Fleet workspace is already materialized")

        workspace = await self._worktrees.prepare(
            repository=self._repository,
            project_id=snapshot.project_id,
            work_id=snapshot.work_id,
            attempt_id=attempt_id,
            base_sha=input_payload.current_sha,
        )
        await self._checked_git(workspace.path, "reset", "--hard", input_payload.current_sha)
        await self._checked_git(workspace.path, "clean", "-fdx")
        await self._worktrees.assert_current(
            workspace,
            expected_sha=input_payload.current_sha,
        )
        cumulative_diff = base64.b64decode(
            input_payload.cumulative_diff_base64,
            validate=True,
        ).decode("utf-8")
        if cumulative_diff:
            await self._apply_patch(workspace.path, cumulative_diff)
        actual_diff, _ = await workspace_diff(workspace)
        if hashlib.sha256(actual_diff.encode()).hexdigest() != snapshot.input_digest:
            raise WorkspaceStaleError("materialized Fleet workspace digest does not match")
        self._materialized[workspace_key] = _MaterializedState(
            path=workspace.path,
            input_tree=await self._write_tree(workspace.path),
        )
        return workspace

    async def capture(
        self,
        snapshot: FleetWorkspaceTransfer,
        workspace: Workspace,
    ) -> FleetWorkspaceTransferResult:
        input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
        if not isinstance(workspace, SoftwareWorkspace):
            raise TypeError("software Fleet resolver requires SoftwareWorkspace")
        workspace_key = (snapshot.project_id, snapshot.work_id, snapshot.ref)
        state = self._materialized.get(workspace_key)
        if state is None or workspace.path != state.path:
            raise WorkspaceStaleError("Fleet workspace was not materialized by this resolver")
        await self._worktrees.assert_current(
            workspace,
            expected_sha=input_payload.current_sha,
        )
        resulting_diff, _ = await workspace_diff(workspace)
        result_content = resulting_diff.encode()
        output_tree = await self._write_tree(workspace.path)
        delta = await self._checked_git(
            workspace.path,
            "diff-tree",
            "--no-commit-id",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "-p",
            state.input_tree,
            output_tree,
            "--",
        )
        delta_content = delta.encode()
        self._materialized.pop(workspace_key)
        output_payload = SoftwareFleetWorkspaceOutput(
            repository_ref=input_payload.repository_ref,
            base_sha=input_payload.base_sha,
            current_sha=input_payload.current_sha,
            delta_diff_base64=base64.b64encode(delta_content).decode("ascii"),
            delta_diff_sha256=hashlib.sha256(delta_content).hexdigest(),
        )
        return FleetWorkspaceTransferResult(
            ref=snapshot.ref,
            project_id=snapshot.project_id,
            work_id=snapshot.work_id,
            kind=snapshot.kind,
            input_digest=snapshot.input_digest,
            result_digest=hashlib.sha256(result_content).hexdigest(),
            payload=output_payload.model_dump(mode="json"),
        )

    @staticmethod
    def _attempt_id(workspace_ref: str) -> str:
        prefix = "workspace://"
        if not workspace_ref.startswith(prefix):
            raise ValueError("Fleet workspace reference is invalid")
        attempt_id = workspace_ref.removeprefix(prefix)
        if not attempt_id or Path(attempt_id).name != attempt_id:
            raise ValueError("Fleet workspace reference is invalid")
        return attempt_id

    @classmethod
    async def _write_tree(cls, path: Path) -> str:
        await cls._checked_git(path, "add", "--all")
        try:
            return (await cls._checked_git(path, "write-tree")).strip()
        finally:
            await cls._checked_git(path, "reset", "--mixed", "HEAD")

    @classmethod
    async def _apply_patch(cls, path: Path, diff: str) -> None:
        await cls._checked_git(
            path,
            "apply",
            "--binary",
            "--whitespace=nowarn",
            "--",
            stdin=diff,
        )

    @staticmethod
    async def _checked_git(path: Path, *args: str, stdin: str = "") -> str:
        result = await run_worker_subprocess(
            argv=("git", *args),
            stdin=stdin,
            cwd=path,
            output_limit=None,
        )
        if result.returncode != 0:
            raise WorkspaceStaleError(result.stderr)
        return result.stdout


class SoftwareFleetTaskHandler:
    """Validate and execute a Fleet task with worker-local native authentication."""

    def __init__(
        self,
        *,
        workspace_resolver: FleetWorkerWorkspaceResolver,
        codex_runtime: CodexRuntime | None = None,
        claude_analysis_runtime: ClaudeRuntime | None = None,
        claude_review_runtime: ClaudeRuntime | None = None,
        harness_tiers: Mapping[str, HarnessTier] | None = None,
        harness_backends: Mapping[str, str] | None = None,
        sandbox: Any = None,
        mcp_connections: McpConnectionResolver | None = None,
        agent_factory: Callable[..., Any] = UniversalAgent,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._codex_runtime = codex_runtime or CodexRuntime()
        self._claude_analysis_runtime = claude_analysis_runtime or ClaudeRuntime()
        self._claude_review_runtime = claude_review_runtime or ClaudeRuntime()
        self._harness_tiers = dict(harness_tiers or {})
        self._harness_backends = dict(harness_backends or {})
        self._sandbox = sandbox
        self._mcp_connections = mcp_connections
        self._agent_factory = agent_factory
        if self._claude_analysis_runtime is self._claude_review_runtime:
            raise ValueError("analysis and review require distinct Claude runtimes")
        if not isinstance(self._codex_runtime, CodexRuntime):
            raise TypeError("runtime.codex requires CodexRuntime")
        if not isinstance(self._claude_analysis_runtime, ClaudeRuntime):
            raise TypeError("runtime.claude analysis requires ClaudeRuntime")
        if not isinstance(self._claude_review_runtime, ClaudeRuntime):
            raise TypeError("runtime.claude review requires ClaudeRuntime")

    async def __call__(self, task: dict[str, Any], context: WorkerTaskContext) -> str:
        payload = FleetOperatorTaskPayload.model_validate(task.get("payload"))
        runtime_capability = self._validate_payload(payload)
        request = payload.request
        if "run_id" not in task or task["run_id"] != request.run_id:
            raise ValueError("top-level run does not match request")
        if "project_id" not in task or task["project_id"] != request.project_id:
            raise ValueError("top-level project does not match request")
        if context.project_id != request.project_id:
            raise ValueError("worker belongs to a different project")
        if not set(payload.required_capabilities).issubset(context.capability_names):
            raise ValueError("worker did not advertise every required capability")
        log: list[str] = []
        progress_sink = BatchingActivitySink(
            lambda batch: context.report_progress(request.run_id, batch)
        )
        activity_sink = _ActivityFanoutSink(log=log, progress_sink=progress_sink)
        try:
            runtime = copy.copy(
                self._runtime(runtime_capability, request.stage, payload.harness_tier)
            )
            runtime._activity_sink = activity_sink
            if payload.workspace is None:
                raise ValueError("software work.operator requires a workspace snapshot")
            workspace = await self._workspace_resolver.materialize(payload.workspace)
            self._validate_materialized_workspace(payload.workspace, workspace)
            result = await runtime.run(
                request,
                payload.capsule,
                payload.capabilities,
                workspace,
            )
            self._validate_result(request, result)
            workspace_result = await self._workspace_resolver.capture(
                payload.workspace, workspace
            )
            self._validate_workspace_result(payload, workspace_result)
            output_payload = SoftwareFleetWorkspaceOutput.model_validate(
                workspace_result.payload
            )
            if not self._can_write(payload.capabilities) and base64.b64decode(
                output_payload.delta_diff_base64,
                validate=True,
            ):
                raise ValueError("read-only operator changed the workspace")
        finally:
            await progress_sink.close()
        return FleetOperatorResultEnvelope(
            result=result,
            workspace_result=workspace_result,
            activity_log=bounded_ndjson(log, FLEET_ACTIVITY_LOG_MAX_BYTES) or None,
        ).model_dump_json()

    def _runtime(
        self,
        runtime_capability: str,
        stage: str,
        harness_tier: str | None,
    ) -> OperatorRuntime:
        if runtime_capability == "runtime.codex":
            if stage not in {"implement", "repair"}:
                raise ValueError(f"runtime.codex does not support stage {stage!r}")
            return self._codex_runtime
        if runtime_capability == "runtime.harness":
            return HarnessRuntime(
                tier=harness_tier,
                tiers=self._harness_tiers,
                backends=self._harness_backends,
                sandbox=self._sandbox,
                mcp_connections=self._mcp_connections,
                agent_factory=self._agent_factory,
            )
        if runtime_capability != "runtime.claude":
            raise ValueError("unsupported native runtime capability")
        if stage in {"analysis", "design"}:
            return self._claude_analysis_runtime
        if stage == "review":
            return self._claude_review_runtime
        raise ValueError(f"runtime.claude does not support stage {stage!r}")

    @staticmethod
    def _validate_payload(payload: FleetOperatorTaskPayload) -> str:
        request = payload.request
        if payload.capsule.project_id != request.project_id:
            raise ValueError("capsule belongs to a different project")
        if payload.capsule.work_id != request.work_id:
            raise ValueError("capsule belongs to different work")
        if payload.capsule.stage != request.stage:
            raise ValueError("capsule belongs to a different stage")
        if payload.capabilities.project_id != request.project_id:
            raise ValueError("capabilities belong to a different project")
        if payload.capabilities.credential_refs():
            raise ValueError("fleet task cannot carry credential references")
        if payload.workspace is not None:
            if payload.workspace.project_id != request.project_id:
                raise ValueError("workspace belongs to a different project")
            if payload.workspace.work_id != request.work_id:
                raise ValueError("workspace belongs to different work")
            if payload.workspace.kind != SOFTWARE_FLEET_WORKSPACE_KIND:
                raise ValueError("workspace transfer is not software Git state")
            SoftwareFleetWorkspaceInput.model_validate(payload.workspace.payload)

        required = payload.required_capabilities
        if len(required) != len(set(required)):
            raise ValueError("required capabilities must be unique")
        runtime_capabilities = tuple(
            name for name in required if name.startswith("runtime.")
        )
        if len(runtime_capabilities) != 1 or runtime_capabilities[0] not in {
            "runtime.codex",
            "runtime.claude",
            "runtime.harness",
        }:
            raise ValueError("work.operator requires exactly one supported runtime")
        expected = {
            runtime_capabilities[0],
            *(grant.name for grant in payload.capabilities.grants),
        }
        if set(required) != expected:
            raise ValueError("required capabilities do not match the capability set")
        return runtime_capabilities[0]

    @staticmethod
    def _validate_materialized_workspace(
        snapshot: FleetWorkspaceTransfer,
        workspace: Workspace,
    ) -> None:
        if workspace.ref != snapshot.ref:
            raise ValueError("materialized workspace reference changed")
        if workspace.project_id != snapshot.project_id:
            raise ValueError("materialized workspace belongs to a different project")
        if workspace.work_id != snapshot.work_id:
            raise ValueError("materialized workspace belongs to different work")

    @staticmethod
    def _validate_result(request: WorkRequest, result: OperatorResult) -> None:
        if (
            result.project_id != request.project_id
            or result.work_id != request.work_id
            or result.run_id != request.run_id
        ):
            raise ValueError("operator result belongs to a different request")

    @staticmethod
    def _validate_workspace_result(
        payload: FleetOperatorTaskPayload,
        result: FleetWorkspaceTransferResult,
    ) -> None:
        snapshot = payload.workspace
        if snapshot is None:
            raise ValueError("software work.operator requires a workspace snapshot")
        input_payload = SoftwareFleetWorkspaceInput.model_validate(snapshot.payload)
        output_payload = SoftwareFleetWorkspaceOutput.model_validate(result.payload)
        if (
            result.ref != snapshot.ref
            or result.project_id != snapshot.project_id
            or result.work_id != snapshot.work_id
            or result.kind != snapshot.kind
            or result.input_digest != snapshot.input_digest
            or output_payload.repository_ref != input_payload.repository_ref
            or output_payload.base_sha != input_payload.base_sha
            or output_payload.current_sha != input_payload.current_sha
        ):
            raise ValueError("workspace result belongs to a different snapshot")

    @staticmethod
    def _can_write(capabilities: CapabilitySet) -> bool:
        return any(
            "workspace.write" in grant.permissions
            for grant in capabilities.grants
        )
