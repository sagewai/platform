# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic operator protocol and worker-local native CLI runtimes."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.fleet.execution import run_worker_subprocess
from sagewai.sandbox.secret_provider import SecretProvider
from sagewai.work.models import (
    ActionIntent,
    ActionResult,
    ActionScope,
    ControlPrecondition,
    TaskCapsule,
)

BoundedText = Annotated[str, Field(max_length=2000)]


class CapabilityGrant(BaseModel):
    """One scoped capability available to an operator attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    name: str
    kind: Literal["mcp", "cli", "api", "browser", "model", "filesystem", "custom"]
    scope: dict[str, Any]
    permissions: tuple[str, ...]
    credential_ref: str | None = None


class CapabilitySet(BaseModel):
    """The complete, explicit capability boundary for an attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    grants: tuple[CapabilityGrant, ...]

    @model_validator(mode="after")
    def validate_grants(self) -> CapabilitySet:
        names: set[str] = set()
        for grant in self.grants:
            if grant.project_id != self.project_id:
                raise ValueError("capability grant belongs to a different project")
            if grant.name in names:
                raise ValueError(f"duplicate capability grant: {grant.name}")
            names.add(grant.name)
        return self

    def for_names(self, names: tuple[str, ...]) -> CapabilitySet:
        """Return grants named by an already-declared ActionScope."""
        requested = set(names)
        return CapabilitySet(
            project_id=self.project_id,
            grants=tuple(grant for grant in self.grants if grant.name in requested),
        )

    def credential_refs(self) -> tuple[str, ...]:
        """Return distinct credential references in grant order."""
        return tuple(
            dict.fromkeys(
                grant.credential_ref for grant in self.grants if grant.credential_ref is not None
            )
        )


class WorkRequest(BaseModel):
    """One bounded request submitted to an OperatorRuntime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str
    run_id: str
    stage: str
    action_scope: ActionScope
    action_intents: tuple[ActionIntent, ...]
    control_preconditions: tuple[ControlPrecondition, ...]

    @model_validator(mode="after")
    def validate_project_scope(self) -> WorkRequest:
        for intent in self.action_intents:
            if intent.project_id != self.project_id:
                raise ValueError("action intent belongs to a different project")
        for precondition in self.control_preconditions:
            if precondition.project_id != self.project_id:
                raise ValueError("control precondition belongs to a different project")
        return self


class OperatorResult(BaseModel):
    """Structured, bounded result returned by every operator runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    work_id: str
    run_id: str
    status: Literal["passed", "failed", "blocked"]
    summary: str = Field(max_length=4000)
    evidence_refs: tuple[BoundedText, ...] = Field(max_length=100)
    artifact_refs: tuple[BoundedText, ...] = Field(max_length=100)
    changes: tuple[BoundedText, ...] = Field(max_length=100)
    verification: tuple[BoundedText, ...] = Field(max_length=100)
    risks: tuple[BoundedText, ...] = Field(max_length=100)
    action_results: tuple[ActionResult, ...] = Field(max_length=100)
    output_tokens: int | None = None
    profile_context: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Workspace(Protocol):
    """Workspace boundary supplied to an OperatorRuntime."""

    ref: str

    @property
    def project_id(self) -> str | None: ...

    work_id: str
    path: Path


@runtime_checkable
class OperatorRuntime(Protocol):
    """Profile-neutral runtime seam."""

    name: str

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult: ...


class _NativeRuntime:
    name: str

    def __init__(
        self,
        *,
        executable: str,
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
    ) -> None:
        self._executable = executable
        self._secret_provider = secret_provider
        self._timeout = timeout

    async def _environment(
        self,
        request: WorkRequest,
        capabilities: CapabilitySet,
    ) -> dict[str, str]:
        credential_refs = list(capabilities.credential_refs())
        if not credential_refs or self._secret_provider is None:
            return {}
        if request.project_id is None:
            raise ValueError("credential-scoped native runs require a project")
        values = await self._secret_provider.env_for(
            project_id=request.project_id,
            run_id=request.run_id,
            agent_id=None,
            declared_scopes=credential_refs,
        )
        return dict(values)

    @staticmethod
    def _prompt(
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
    ) -> str:
        return json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "capsule": capsule.model_dump(mode="json"),
                "capabilities": capabilities.model_dump(mode="json"),
            },
            sort_keys=True,
        )

    @staticmethod
    def _validate_result(payload: Any, request: WorkRequest) -> OperatorResult:
        result = OperatorResult.model_validate(payload)
        if (
            result.project_id != request.project_id
            or result.work_id != request.work_id
            or result.run_id != request.run_id
        ):
            raise ValueError("operator result belongs to a different request")
        return result


class CodexRuntime(_NativeRuntime):
    """Ephemeral Codex CLI execution in an isolated worker workspace."""

    name = "codex"

    def __init__(
        self,
        *,
        executable: str = "codex",
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
    ) -> None:
        super().__init__(
            executable=executable,
            secret_provider=secret_provider,
            timeout=timeout,
        )

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        if workspace is None:
            raise ValueError("CodexRuntime requires a workspace")
        environment = await self._environment(request, capabilities)
        with tempfile.TemporaryDirectory(prefix="sagewai-codex-") as temporary:
            result_path = Path(temporary) / "result.json"
            schema_path = Path(temporary) / "schema.json"
            schema_path.write_text(json.dumps(OperatorResult.model_json_schema()))
            process = await run_worker_subprocess(
                argv=(
                    self._executable,
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--cd",
                    str(workspace.path),
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(result_path),
                    "-",
                ),
                stdin=self._prompt(request, capsule, capabilities),
                explicit_env=environment,
                cwd=workspace.path,
                timeout=self._timeout,
            )
            if process.returncode != 0:
                return _failed_result(request, process.stderr)
            payload = {
                **json.loads(result_path.read_text()),
                "output_tokens": None,
            }
            return self._validate_result(payload, request)


class ClaudeRuntime(_NativeRuntime):
    """Non-persistent Claude CLI execution in an isolated worker workspace."""

    name = "claude"

    def __init__(
        self,
        *,
        executable: str = "claude",
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
    ) -> None:
        super().__init__(
            executable=executable,
            secret_provider=secret_provider,
            timeout=timeout,
        )

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        if workspace is None:
            raise ValueError("ClaudeRuntime requires a workspace")
        builtin_tools, allowed_tools = _claude_tool_scope(capabilities)
        argv = [
            self._executable,
            "--print",
            "--no-session-persistence",
            "--safe-mode",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--tools",
            ",".join(builtin_tools),
        ]
        if allowed_tools:
            argv.extend(("--allowedTools", ",".join(allowed_tools)))
        argv.extend(
            (
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(OperatorResult.model_json_schema(), sort_keys=True),
            )
        )
        process = await run_worker_subprocess(
            argv=argv,
            stdin=self._prompt(request, capsule, capabilities),
            explicit_env=await self._environment(request, capabilities),
            cwd=workspace.path,
            timeout=self._timeout,
        )
        if process.returncode != 0:
            return _failed_result(request, process.stderr)
        envelope = json.loads(process.stdout)
        payload = {
            **envelope["structured_output"],
            "output_tokens": envelope.get("usage", {}).get("output_tokens"),
        }
        return self._validate_result(payload, request)


def _claude_tool_scope(
    capabilities: CapabilitySet,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    builtin_tools: set[str] = set()
    allowed_tools: set[str] = set()
    for grant in capabilities.grants:
        if grant.kind == "cli":
            command = _capability_suffix(grant.name, "cli", r"[A-Za-z0-9._+-]+")
            builtin_tools.add("Bash")
            allowed_tools.add(f"Bash({command} *)")
        elif grant.kind == "mcp":
            server = _capability_suffix(grant.name, "mcp", r"[A-Za-z0-9_-]+")
            allowed_tools.add(f"mcp__{server}__*")
        elif grant.kind == "filesystem":
            permissions = set(grant.permissions)
            can_write = "workspace.write" in permissions
            can_read = can_write or "workspace.read" in permissions
            if not can_read:
                raise ValueError("filesystem grant requires workspace.read or workspace.write")
            roots = grant.scope.get("roots")
            if not isinstance(roots, (list, tuple)) or not roots:
                raise ValueError("filesystem grant requires scoped roots")
            patterns = tuple(_claude_workspace_pattern(root) for root in roots)
            builtin_tools.update(("Glob", "Grep", "Read"))
            allowed_tools.update(f"Read({pattern})" for pattern in patterns)
            if can_write:
                builtin_tools.update(("Edit", "Write"))
                allowed_tools.update(f"Edit({pattern})" for pattern in patterns)
    return tuple(sorted(builtin_tools)), tuple(sorted(allowed_tools))


def _capability_suffix(name: str, kind: str, pattern: str) -> str:
    prefix = f"{kind}:"
    value = name.removeprefix(prefix)
    if value == name or re.fullmatch(pattern, value) is None:
        raise ValueError(f"{kind} capability name must match {prefix}<name>")
    return value


def _claude_workspace_pattern(root: object) -> str:
    if not isinstance(root, str) or not root:
        raise ValueError("filesystem roots must be non-empty relative paths")
    path = PurePosixPath(root)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("filesystem roots must stay inside the workspace")
    normalized = path.as_posix().strip("/")
    return "/**" if normalized in {"", "."} else f"/{normalized}/**"


def _failed_result(request: WorkRequest, error: str) -> OperatorResult:
    return OperatorResult(
        project_id=request.project_id,
        work_id=request.work_id,
        run_id=request.run_id,
        status="failed",
        summary=error[:4000],
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={},
    )
