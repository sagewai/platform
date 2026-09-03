# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Read-only native CLI capability discovery and model/effort selection."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from sagewai.artifacts.object_store import ArtifactStore
from sagewai.fleet.execution import WorkerConfigurationError, run_worker_subprocess
from sagewai.work.activity import ActivitySink
from sagewai.work.runtime import (
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    OperatorResult,
    TaskCapsule,
    WorkRequest,
    Workspace,
)

RuntimeKind = Literal["runtime.codex", "runtime.claude"]


class RuntimeCapabilityProbeError(WorkerConfigurationError):
    """The installed native CLI did not return a trustworthy capability catalog."""


class RuntimeModelCapability(BaseModel):
    """One model and the effort values currently advertised by its CLI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    resolved_model: str
    supported_efforts: tuple[str, ...]
    default_effort: str | None = None
    priority: int = 0

    @field_validator("model", "resolved_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("runtime model names must be non-empty and trimmed")
        return value

    @field_validator("supported_efforts")
    @classmethod
    def validate_efforts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or value != value.strip() for value in values):
            raise ValueError("runtime efforts must be non-empty and trimmed")
        if len(values) != len(set(values)):
            raise ValueError("runtime efforts must be unique")
        return values

    @model_validator(mode="after")
    def validate_default_effort(self) -> RuntimeModelCapability:
        if (
            self.default_effort is not None
            and self.default_effort not in self.supported_efforts
        ):
            raise ValueError("default runtime effort is not supported by the model")
        return self


class RuntimeCapabilitySnapshot(BaseModel):
    """A bounded, PII-free snapshot from one installed native CLI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: RuntimeKind
    cli_version: str
    default_model: str
    models: tuple[RuntimeModelCapability, ...]

    @model_validator(mode="after")
    def validate_catalog(self) -> RuntimeCapabilitySnapshot:
        if not self.models:
            raise ValueError("runtime capability catalog is empty")
        names = [model.model for model in self.models]
        if len(names) != len(set(names)):
            raise ValueError("runtime capability catalog contains duplicate models")
        if self.default_model not in names:
            raise ValueError("runtime default model is not in the catalog")
        return self


class RuntimeSelection(BaseModel):
    """One exact model/effort pair selected from a live capability snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: RuntimeKind
    model: str
    effort: str | None
    requested_model: str | None
    requested_effort: str | None
    policy_reason: str | None = None
    fallback_reason: str | None = None

    def verification_text(self) -> str:
        selected = f"{self.runtime}: model={self.model}"
        if self.effort is not None:
            selected += f", effort={self.effort}"
        details = tuple(
            detail
            for detail in (self.policy_reason, self.fallback_reason)
            if detail is not None
        )
        if details:
            selected += f" ({'; '.join(details)})"
        return selected


def parse_codex_models(payload: str, *, cli_version: str) -> RuntimeCapabilitySnapshot:
    """Parse the PII-free JSON emitted by codex debug models."""

    try:
        raw = json.loads(payload)
        candidates = raw["models"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeCapabilityProbeError("Codex model catalog is invalid") from exc
    models: list[RuntimeModelCapability] = []
    for candidate in candidates:
        if candidate.get("visibility") != "list":
            continue
        if candidate.get("supported_in_api") is False:
            continue
        try:
            efforts = tuple(
                level["effort"] for level in candidate["supported_reasoning_levels"]
            )
            models.append(
                RuntimeModelCapability(
                    model=candidate["slug"],
                    resolved_model=candidate["slug"],
                    supported_efforts=efforts,
                    default_effort=candidate.get("default_reasoning_level"),
                    priority=int(candidate.get("priority", 0)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeCapabilityProbeError("Codex model entry is invalid") from exc
    if not models:
        raise RuntimeCapabilityProbeError("Codex model catalog has no usable models")
    models.sort(key=lambda model: (model.priority, model.model))
    return RuntimeCapabilitySnapshot(
        runtime="runtime.codex",
        cli_version=cli_version,
        default_model=models[0].model,
        models=tuple(models),
    )


def parse_claude_initialize(
    payload: str,
    *,
    cli_version: str,
) -> RuntimeCapabilitySnapshot:
    """Parse Claude's control response while deliberately discarding account data."""

    models_payload: list[dict[str, Any]] | None = None
    for line in payload.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("type") != "control_response":
            continue
        response = message.get("response", {}).get("response", {})
        if isinstance(response.get("models"), list):
            models_payload = response["models"]
            break
    if models_payload is None:
        raise RuntimeCapabilityProbeError("Claude initialize response has no model catalog")

    models: list[RuntimeModelCapability] = []
    default_model: str | None = None
    for priority, candidate in enumerate(models_payload):
        if candidate.get("disabled"):
            continue
        try:
            model = str(candidate["value"])
            resolved = str(candidate["resolvedModel"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeCapabilityProbeError("Claude model entry is invalid") from exc
        supports_effort = bool(candidate.get("supportsEffort"))
        levels = candidate.get("supportedEffortLevels") if supports_effort else ()
        efforts = tuple(str(level) for level in (levels or ()))
        models.append(
            RuntimeModelCapability(
                model=model,
                resolved_model=resolved,
                supported_efforts=efforts,
                default_effort=None,
                priority=priority,
            )
        )
        if default_model is None:
            default_model = model
        if model == "default":
            default_model = model
    if not models or default_model is None:
        raise RuntimeCapabilityProbeError("Claude model catalog has no usable models")
    return RuntimeCapabilitySnapshot(
        runtime="runtime.claude",
        cli_version=cli_version,
        default_model=default_model,
        models=tuple(models),
    )


async def probe_runtime_capabilities(
    runtime: RuntimeKind,
    *,
    executable: str | None = None,
    timeout: float = 30,
) -> RuntimeCapabilitySnapshot:
    """Query an installed CLI without starting a model inference turn."""

    command = executable or ("codex" if runtime == "runtime.codex" else "claude")
    version = await run_worker_subprocess(
        argv=(command, "--version"),
        timeout=timeout,
        output_limit=4096,
    )
    if version.returncode != 0:
        raise RuntimeCapabilityProbeError(
            f"{runtime} version probe failed: {version.stderr[:500]}"
        )
    cli_version = version.stdout.strip()

    if runtime == "runtime.codex":
        result = await run_worker_subprocess(
            argv=(command, "debug", "models"),
            timeout=timeout,
            output_limit=2_000_000,
        )
        if result.returncode != 0:
            raise RuntimeCapabilityProbeError(
                f"Codex capability probe failed: {result.stderr[:500]}"
            )
        return parse_codex_models(result.stdout, cli_version=cli_version)

    initialize = json.dumps(
        {
            "type": "control_request",
            "request_id": "sagewai-runtime-capability-probe",
            "request": {"subtype": "initialize"},
        }
    )
    result = await run_worker_subprocess(
        argv=(
            command,
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--safe-mode",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "",
        ),
        stdin=initialize + "\n",
        timeout=timeout,
        output_limit=500_000,
    )
    if result.returncode != 0:
        raise RuntimeCapabilityProbeError(
            f"Claude capability probe failed: {result.stderr[:500]}"
        )
    return parse_claude_initialize(result.stdout, cli_version=cli_version)


def select_runtime_configuration(
    snapshot: RuntimeCapabilitySnapshot,
    *,
    requested_model: str | None,
    requested_effort: str | None,
) -> RuntimeSelection:
    """Resolve a supported pair, falling back only within the probed catalog."""

    def aliases(model: RuntimeModelCapability) -> set[str]:
        return {
            model.model,
            model.resolved_model,
            model.resolved_model.split("[", 1)[0],
        }

    selected_model = next(
        (
            model
            for model in snapshot.models
            if requested_model is not None and requested_model in aliases(model)
        ),
        None,
    )
    reasons: list[str] = []
    output_model = requested_model
    if selected_model is None:
        selected_model = next(
            model for model in snapshot.models if model.model == snapshot.default_model
        )
        output_model = selected_model.model
        if requested_model is not None:
            reasons.append(
                f"requested model {requested_model} is unavailable; used provider default"
            )
    assert output_model is not None

    effort = requested_effort
    if effort is not None and effort not in selected_model.supported_efforts:
        if selected_model.supported_efforts:
            effort = selected_model.supported_efforts[-1]
            reasons.append(
                f"requested effort {requested_effort} is unsupported; used advertised maximum"
            )
        else:
            provider_default = next(
                model for model in snapshot.models if model.model == snapshot.default_model
            )
            if provider_default.supported_efforts:
                selected_model = provider_default
                output_model = provider_default.model
                effort = provider_default.supported_efforts[-1]
                reasons.append(
                    "requested model does not support effort; used provider default maximum"
                )
            else:
                effort = None
                reasons.append("requested effort is unavailable for the selected runtime")
    return RuntimeSelection(
        runtime=snapshot.runtime,
        model=output_model,
        effort=effort,
        requested_model=requested_model,
        requested_effort=requested_effort,
        fallback_reason="; ".join(reasons) or None,
    )


def select_codex_task_configuration(
    snapshot: RuntimeCapabilitySnapshot,
    *,
    stage: str,
    risk: str,
    design_required: bool,
    bounded_model: str | None,
    requested_effort: str | None,
) -> RuntimeSelection:
    """Select Codex from accepted task evidence and the live model catalog."""

    if snapshot.runtime != "runtime.codex":
        raise ValueError("Codex task selection requires a Codex capability snapshot")
    complex_work = stage == "repair" or risk == "high" or design_required
    model = None if complex_work else bounded_model
    model_selection = select_runtime_configuration(
        snapshot,
        requested_model=model,
        requested_effort=None,
    )
    selected = next(
        capability
        for capability in snapshot.models
        if model_selection.model
        in {
            capability.model,
            capability.resolved_model,
            capability.resolved_model.split("[", 1)[0],
        }
    )
    if not selected.supported_efforts:
        raise RuntimeCapabilityProbeError(
            f"Codex model {selected.model} advertises no reasoning efforts"
        )
    maximum_effort = selected.supported_efforts[-1]
    selection = select_runtime_configuration(
        snapshot,
        requested_model=model_selection.model,
        requested_effort=maximum_effort,
    )
    policy_override = (
        f"requested effort {requested_effort} overridden by Work policy maximum "
        f"{maximum_effort}"
        if requested_effort is not None and requested_effort != maximum_effort
        else None
    )
    fallback_reasons = tuple(
        reason
        for reason in (
            model_selection.fallback_reason,
            policy_override,
            selection.fallback_reason,
        )
        if reason is not None
    )
    return selection.model_copy(
        update={
            "requested_effort": requested_effort,
            "policy_reason": (
                "complex or repair Work policy"
                if complex_work
                else "bounded implementation Work policy"
            ),
            "fallback_reason": "; ".join(fallback_reasons) or None,
        }
    )


class RefreshingCodexRuntime(CodexRuntime):
    """Codex runtime whose selected pair is refreshed from the installed CLI."""

    def __init__(
        self,
        *,
        snapshot: RuntimeCapabilitySnapshot,
        requested_model: str | None,
        requested_effort: str | None,
        executable: str = "codex",
        timeout: float = 1800,
        probe_timeout: float = 30,
        refresh_interval_seconds: float = 3600,
        activity_sink: ActivitySink | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("runtime capability refresh interval must be positive")
        self._requested_model = requested_model
        self._requested_effort = requested_effort
        self._probe_timeout = probe_timeout
        self._refresh_interval_seconds = refresh_interval_seconds
        self._last_probe = time.monotonic()
        self._refresh_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._snapshot = snapshot
        selection = select_codex_task_configuration(
            snapshot,
            stage="implement",
            risk="low",
            design_required=False,
            bounded_model=requested_model,
            requested_effort=requested_effort,
        )
        super().__init__(
            executable=executable,
            timeout=timeout,
            model=selection.model,
            reasoning_effort=selection.effort,
            selection_note=selection.verification_text(),
            activity_sink=activity_sink,
            artifact_store=artifact_store,
        )

    async def _refresh_if_due(self) -> None:
        if time.monotonic() - self._last_probe < self._refresh_interval_seconds:
            return
        async with self._refresh_lock:
            if time.monotonic() - self._last_probe < self._refresh_interval_seconds:
                return
            snapshot = await probe_runtime_capabilities(
                "runtime.codex",
                executable=self._executable,
                timeout=self._probe_timeout,
            )
            self._snapshot = snapshot
            self._last_probe = time.monotonic()

    def _select_for_task(self, request: WorkRequest, capsule: TaskCapsule) -> None:
        selection = select_codex_task_configuration(
            self._snapshot,
            stage=request.stage,
            risk=capsule.contract.risk,
            design_required=capsule.contract.design_required,
            bounded_model=self._requested_model,
            requested_effort=self._requested_effort,
        )
        self._model = selection.model
        self._reasoning_effort = selection.effort
        self._selection_note = selection.verification_text()

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        async with self._run_lock:
            await self._refresh_if_due()
            self._select_for_task(request, capsule)
            return await super().run(request, capsule, capabilities, workspace)


class RefreshingClaudeRuntime(ClaudeRuntime):
    """Claude runtime whose selected pair is refreshed from the installed CLI."""

    def __init__(
        self,
        *,
        snapshot: RuntimeCapabilitySnapshot,
        requested_model: str | None,
        requested_effort: str | None,
        max_budget_usd: str | None,
        executable: str = "claude",
        timeout: float = 1800,
        probe_timeout: float = 30,
        refresh_interval_seconds: float = 3600,
        activity_sink: ActivitySink | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("runtime capability refresh interval must be positive")
        self._requested_model = requested_model
        self._requested_effort = requested_effort
        self._probe_timeout = probe_timeout
        self._refresh_interval_seconds = refresh_interval_seconds
        self._last_probe = time.monotonic()
        self._refresh_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        selection = select_runtime_configuration(
            snapshot,
            requested_model=requested_model,
            requested_effort=requested_effort,
        )
        super().__init__(
            executable=executable,
            timeout=timeout,
            model=selection.model,
            effort=selection.effort,
            max_budget_usd=max_budget_usd,
            selection_note=selection.verification_text(),
            activity_sink=activity_sink,
            artifact_store=artifact_store,
        )

    async def _refresh_if_due(self) -> None:
        if time.monotonic() - self._last_probe < self._refresh_interval_seconds:
            return
        async with self._refresh_lock:
            if time.monotonic() - self._last_probe < self._refresh_interval_seconds:
                return
            snapshot = await probe_runtime_capabilities(
                "runtime.claude",
                executable=self._executable,
                timeout=self._probe_timeout,
            )
            selection = select_runtime_configuration(
                snapshot,
                requested_model=self._requested_model,
                requested_effort=self._requested_effort,
            )
            self._model = selection.model
            self._effort = selection.effort
            self._selection_note = selection.verification_text()
            self._last_probe = time.monotonic()

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        async with self._run_lock:
            await self._refresh_if_due()
            return await super().run(request, capsule, capabilities, workspace)
