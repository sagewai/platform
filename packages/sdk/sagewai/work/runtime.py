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
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.artifacts.object_store import ArtifactStore
from sagewai.fleet.execution import run_worker_subprocess
from sagewai.sandbox.secret_provider import SecretProvider
from sagewai.work.activity import (
    ActivitySink,
    activity_pipeline,
    archive_activity_log,
)
from sagewai.work.activity_parsers import (
    claude_result_from_line,
    parse_claude_stream_line,
    parse_codex_json_line,
)
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
        if self.action_scope.project_id != self.project_id:
            raise ValueError("action scope belongs to a different project")
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
    input_tokens: int | None = None
    cost_usd: float | None = None
    profile_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action_results(self) -> OperatorResult:
        for result in self.action_results:
            if result.project_id != self.project_id:
                raise ValueError("action result belongs to a different project")
        return self


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


def build_operator_prompt(
    request: WorkRequest,
    capsule: TaskCapsule,
    capabilities: CapabilitySet,
) -> str:
    expected_profile_identity = {
        "project_id": request.project_id,
        "work_id": request.work_id,
        "run_id": request.run_id,
        "attempt_id": request.run_id,
    }
    required_profile_context: dict[str, dict[str, Any]] = {}
    for key, schema in capsule.profile_context.items():
        if not key.endswith("_result_schema") or not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        identity = (
            {
                field: value
                for field, value in expected_profile_identity.items()
                if field in properties
            }
            if isinstance(properties, dict)
            else {}
        )
        result_requirement: dict[str, Any] = {
            "schema_ref": f"capsule.profile_context.{key}"
        }
        if identity:
            result_requirement["identity"] = identity
        required_profile_context[key.removesuffix("_schema")] = result_requirement
    result_contract = {
        "identity": {
            "project_id": request.project_id,
            "work_id": request.work_id,
            "run_id": request.run_id,
        },
        "required_action_results": [
            {
                "project_id": intent.project_id,
                "action_id": intent.action_id,
            }
            for intent in request.action_intents
        ],
        "required_profile_context": required_profile_context,
        "rules": [
            "Return exactly one OperatorResult JSON object matching the output schema.",
            "Copy result_contract.identity exactly into the result identity fields.",
            "Return one action_results receipt for every required_action_results entry and no undeclared action receipts.",
            "For every required_profile_context entry, place the result under that exact profile_context key and make it match the referenced schema.",
            "When a required_profile_context entry contains identity, copy it exactly into the profile result identity fields.",
            "Do not place required profile result fields directly at the profile_context root.",
            "Ground every evidence reference in material actually observed or produced.",
        ],
    }
    return json.dumps(
        {
            "result_contract": result_contract,
            "request": request.model_dump(mode="json"),
            "capsule": capsule.model_dump(mode="json"),
            "capabilities": capabilities.model_dump(mode="json"),
        },
        sort_keys=True,
    )


class _NativeRuntime:
    name: str

    def __init__(
        self,
        *,
        executable: str,
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
        selection_note: str | None = None,
        activity_sink: ActivitySink | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._executable = executable
        self._secret_provider = secret_provider
        self._timeout = timeout
        self._activity_sink = activity_sink
        self._artifact_store = artifact_store
        self._selection_note = _validate_runtime_value(
            "runtime selection evidence",
            selection_note,
        )

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
        return build_operator_prompt(request, capsule, capabilities)

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

    def _with_selection_evidence(self, result: OperatorResult) -> OperatorResult:
        if self._selection_note is None:
            return result
        return OperatorResult.model_validate(
            {
                **result.model_dump(),
                "verification": (*result.verification, self._selection_note),
            }
        )

    def _archive_log(
        self,
        request: WorkRequest,
        log: list[str],
        result: OperatorResult,
    ) -> OperatorResult:
        return archive_activity_log(
            self._artifact_store,
            request,
            log,
            result,
            created_by=self.name,
        )


def _codex_result_schema() -> dict[str, Any]:
    schema = OperatorResult.model_json_schema()
    properties = schema["properties"]
    profile_context = properties["profile_context"]
    profile_context["additionalProperties"] = False
    profile_context["properties"] = {}
    for name in ("output_tokens", "input_tokens", "cost_usd"):
        properties[name].pop("default", None)
    schema["required"] = list(properties)
    return schema


class CodexRuntime(_NativeRuntime):
    """Ephemeral Codex CLI execution in an isolated worker workspace."""

    name = "codex"

    def __init__(
        self,
        *,
        executable: str = "codex",
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
        model: str | None = None,
        reasoning_effort: str | None = None,
        selection_note: str | None = None,
        activity_sink: ActivitySink | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        super().__init__(
            executable=executable,
            secret_provider=secret_provider,
            timeout=timeout,
            selection_note=selection_note,
            activity_sink=activity_sink,
            artifact_store=artifact_store,
        )
        self._model = _validate_runtime_value("Codex model", model)
        self._reasoning_effort = _validate_runtime_value(
            "Codex reasoning effort",
            reasoning_effort,
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
        counter, emit, log = activity_pipeline(request, environment, self._activity_sink)
        with tempfile.TemporaryDirectory(prefix="sagewai-codex-") as temporary:
            result_path = Path(temporary) / "result.json"
            schema_path = Path(temporary) / "schema.json"
            schema_path.write_text(json.dumps(_codex_result_schema()))
            argv = [
                self._executable,
                "exec",
            ]
            if self._model is not None:
                argv.extend(("--model", self._model))
            if self._reasoning_effort is not None:
                argv.extend(("-c", f"model_reasoning_effort={self._reasoning_effort}"))
            argv.extend(
                (
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--cd",
                    str(workspace.path),
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(result_path),
                    "--json",
                    "-",
                )
            )

            def on_stdout_line(line: str) -> None:
                for activity in parse_codex_json_line(line, counter):
                    emit(activity)

            process = await run_worker_subprocess(
                argv=argv,
                stdin=self._prompt(request, capsule, capabilities),
                explicit_env=environment,
                cwd=workspace.path,
                timeout=self._timeout,
                output_limit=None,
                on_stdout_line=on_stdout_line,
                on_stderr_line=lambda line: emit(
                    counter.next(source="codex", kind="raw", summary=line)
                ),
            )
            if process.returncode != 0:
                return self._archive_log(
                    request,
                    log,
                    self._with_selection_evidence(
                        _failed_result(request, process.stderr)
                    ),
                )
            payload = {
                **json.loads(result_path.read_text()),
                "output_tokens": None,
                "input_tokens": None,
                "cost_usd": None,
            }
            return self._archive_log(
                request,
                log,
                self._with_selection_evidence(
                    self._validate_result(payload, request)
                ),
            )


class ClaudeRuntime(_NativeRuntime):
    """Non-persistent Claude CLI execution in an isolated worker workspace."""

    name = "claude"

    def __init__(
        self,
        *,
        executable: str = "claude",
        secret_provider: SecretProvider | None = None,
        timeout: float = 1800,
        model: str | None = None,
        effort: str | None = None,
        max_budget_usd: str | None = None,
        selection_note: str | None = None,
        activity_sink: ActivitySink | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        super().__init__(
            executable=executable,
            secret_provider=secret_provider,
            timeout=timeout,
            selection_note=selection_note,
            activity_sink=activity_sink,
            artifact_store=artifact_store,
        )
        self._model = _validate_runtime_value("Claude model", model)
        self._effort = _validate_runtime_value("Claude effort", effort)
        self._max_budget_usd = _validate_positive_number(
            "Claude max budget USD",
            max_budget_usd,
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
        schema_json = json.dumps(OperatorResult.model_json_schema(), sort_keys=True)
        argv = [
            self._executable,
        ]
        if self._model is not None:
            argv.extend(("--model", self._model))
        if self._effort is not None:
            argv.extend(("--effort", self._effort))
        if self._max_budget_usd is not None:
            argv.extend(("--max-budget-usd", self._max_budget_usd))
        argv.extend(
            (
                "--print",
                "--no-session-persistence",
                "--safe-mode",
                "--strict-mcp-config",
                "--permission-mode",
                "dontAsk",
                "--tools",
                ",".join(builtin_tools),
            )
        )
        if allowed_tools:
            argv.extend(("--allowedTools", ",".join(allowed_tools)))
        stream_argv = [
            *argv,
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            schema_json,
        ]
        environment = await self._environment(request, capabilities)
        counter, emit, log = activity_pipeline(request, environment, self._activity_sink)
        claude_result: dict[str, Any] | None = None

        def on_stdout_line(line: str) -> None:
            nonlocal claude_result
            for activity in parse_claude_stream_line(line, counter):
                emit(activity)
            result = claude_result_from_line(line)
            if result is not None:
                claude_result = result

        process = await run_worker_subprocess(
            argv=stream_argv,
            stdin=self._prompt(request, capsule, capabilities),
            explicit_env=environment,
            cwd=workspace.path,
            timeout=self._timeout,
            output_limit=None,
            on_stdout_line=on_stdout_line,
            on_stderr_line=lambda line: emit(
                counter.next(source="claude", kind="raw", summary=line)
            ),
        )
        if process.returncode != 0:
            return self._archive_log(
                request,
                log,
                self._with_selection_evidence(
                    _failed_result(request, process.stderr)
                ),
            )
        if claude_result is not None and "structured_output" in claude_result:
            usage = claude_result.get("usage", {})
            payload = {
                **claude_result["structured_output"],
                "output_tokens": usage.get("output_tokens"),
                "input_tokens": usage.get("input_tokens"),
                "cost_usd": claude_result.get("total_cost_usd"),
            }
            return self._archive_log(
                request,
                log,
                self._with_selection_evidence(
                    self._validate_result(payload, request)
                ),
            )

        fallback_note = (
            "claude: stream-json result lacked structured_output; fallback to --output-format json"
        )
        fallback_argv = [
            *argv,
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
        ]
        fallback = await run_worker_subprocess(
            argv=fallback_argv,
            stdin=self._prompt(request, capsule, capabilities),
            explicit_env=environment,
            cwd=workspace.path,
            timeout=self._timeout,
            output_limit=None,
            on_stderr_line=lambda line: emit(
                counter.next(source="claude", kind="raw", summary=line)
            ),
        )
        if fallback.returncode != 0:
            result = _failed_result(request, fallback.stderr)
            result = result.model_copy(
                update={"verification": (*result.verification, fallback_note)}
            )
            return self._archive_log(
                request,
                log,
                self._with_selection_evidence(result),
            )
        envelope = json.loads(fallback.stdout)
        usage = envelope.get("usage", {})
        payload = {
            **envelope["structured_output"],
            "output_tokens": usage.get("output_tokens"),
            "input_tokens": usage.get("input_tokens"),
            "cost_usd": envelope.get("total_cost_usd"),
        }
        result = self._validate_result(payload, request)
        result = result.model_copy(
            update={"verification": (*result.verification, fallback_note)}
        )
        return self._archive_log(
            request,
            log,
            self._with_selection_evidence(result),
        )


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
            if not isinstance(roots, list | tuple) or not roots:
                raise ValueError("filesystem grant requires scoped roots")
            patterns = tuple(_claude_workspace_pattern(root) for root in roots)
            builtin_tools.update(("Glob", "Grep", "Read"))
            allowed_tools.update(f"Read({pattern})" for pattern in patterns)
            if can_write:
                builtin_tools.update(("Edit", "Write"))
                allowed_tools.update(f"Edit({pattern})" for pattern in patterns)
    return tuple(sorted(builtin_tools)), tuple(sorted(allowed_tools))


def _validate_runtime_value(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")
    return value


def _validate_positive_number(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


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
        summary=error[-4000:],
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={},
    )
