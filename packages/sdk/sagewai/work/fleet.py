# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""OperatorRuntime adapter for the existing durable Fleet task queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.artifacts.object_store import ArtifactStore
from sagewai.fleet.dispatcher import TaskStore
from sagewai.fleet.models import (
    WORKER_ID_ROUTING_LABEL,
    WorkerApprovalStatus,
    WorkerRecord,
    capability_routing_labels,
)
from sagewai.fleet.registry import FleetRegistry
from sagewai.work.models import TaskCapsule
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorResult,
    WorkRequest,
    Workspace,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class FleetWorkspaceTransfer(BaseModel):
    """Profile-neutral opaque workspace input sent to one selected worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    project_id: str | None
    work_id: str
    kind: str
    input_digest: str = Field(pattern=_SHA256_PATTERN)
    payload: dict[str, Any]


class FleetWorkspaceTransferResult(BaseModel):
    """Profile-neutral opaque workspace result returned by one worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    project_id: str | None
    work_id: str
    kind: str
    input_digest: str = Field(pattern=_SHA256_PATTERN)
    result_digest: str = Field(pattern=_SHA256_PATTERN)
    payload: dict[str, Any]


class FleetOperatorTaskPayload(BaseModel):
    """Typed, credential-free Work task transported over Fleet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["work.operator"] = "work.operator"
    request: WorkRequest
    capsule: TaskCapsule
    capabilities: CapabilitySet
    required_capabilities: tuple[str, ...]
    harness_tier: Literal["simple", "medium", "complex"] | None = None
    workspace: FleetWorkspaceTransfer | None

    @model_validator(mode="after")
    def validate_transport_boundary(self) -> FleetOperatorTaskPayload:
        if any(grant.credential_ref is not None for grant in self.capabilities.grants):
            raise ValueError("Fleet task capabilities must not carry credential references")
        if (
            self.capsule.project_id != self.request.project_id
            or self.capsule.work_id != self.request.work_id
            or self.capsule.stage != self.request.stage
            or self.capabilities.project_id != self.request.project_id
        ):
            raise ValueError("Fleet task inputs belong to a different request")
        if self.workspace is not None and (
            self.workspace.project_id != self.request.project_id
            or self.workspace.work_id != self.request.work_id
        ):
            raise ValueError("Fleet workspace belongs to a different request")
        if ("runtime.harness" in self.required_capabilities) != (
            self.harness_tier is not None
        ):
            raise ValueError("harness_tier is required for runtime.harness and forbidden otherwise")
        return self


class FleetOperatorResultEnvelope(BaseModel):
    """Typed result transported from a Fleet worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["work.operator.result"] = "work.operator.result"
    result: OperatorResult
    workspace_result: FleetWorkspaceTransferResult | None
    activity_log: str | None


class FleetWorkspaceTransport(Protocol):
    """Profile-owned central snapshot and returned-delta application seam."""

    async def snapshot(self, workspace: Workspace) -> FleetWorkspaceTransfer: ...

    async def apply(
        self,
        workspace: Workspace,
        snapshot: FleetWorkspaceTransfer,
        result: FleetWorkspaceTransferResult,
    ) -> None: ...


class NoCompatibleWorkerError(RuntimeError):
    """No approved online worker can satisfy a Work stage."""


class FleetOperatorRuntime:
    """Dispatch one Work stage to one deterministically selected Fleet worker."""

    def __init__(
        self,
        *,
        store: TaskStore,
        registry: FleetRegistry,
        org_id: str,
        runtime_capability: str,
        poll_interval_seconds: float,
        heartbeat_ttl: timedelta,
        workspace_transport: FleetWorkspaceTransport | None = None,
        artifact_store: ArtifactStore | None = None,
        harness_tier: Literal["simple", "medium", "complex"] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if heartbeat_ttl <= timedelta(0):
            raise ValueError("heartbeat_ttl must be positive")
        self._store = store
        self._registry = registry
        self._org_id = org_id
        self._runtime_capability = runtime_capability
        self._harness_tier = harness_tier
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_ttl = heartbeat_ttl
        self._workspace_transport = workspace_transport
        self._artifact_store = artifact_store
        self.name = f"fleet:{org_id}:{runtime_capability}"

    async def _select_worker(
        self,
        *,
        project_id: str | None,
        required_capabilities: tuple[str, ...],
        preferred_worker_id: str | None = None,
    ) -> WorkerRecord:
        workers = await self._registry.list_workers(
            self._org_id,
            status=WorkerApprovalStatus.APPROVED,
            pool="default",
            project_id=project_id,
        )
        now = datetime.now(timezone.utc)
        required = set(required_capabilities)
        compatible = sorted(
            (
                worker
                for worker in workers
                if worker.is_online(now=now, heartbeat_ttl=self._heartbeat_ttl)
                and required.issubset(worker.capabilities.capability_names)
            ),
            key=lambda worker: (worker.registered_at, worker.id),
        )
        if preferred_worker_id is not None:
            preferred = next(
                (worker for worker in compatible if worker.id == preferred_worker_id),
                None,
            )
            if preferred is not None:
                return preferred
        if not compatible:
            raise NoCompatibleWorkerError(
                "no approved online worker satisfies the required stage capabilities"
            )
        return compatible[0]

    @staticmethod
    def _transport_capabilities(capabilities: CapabilitySet) -> CapabilitySet:
        return CapabilitySet(
            project_id=capabilities.project_id,
            grants=tuple(
                grant.model_copy(update={"credential_ref": None}) for grant in capabilities.grants
            ),
        )

    async def _snapshot(self, workspace: Workspace | None) -> FleetWorkspaceTransfer | None:
        if workspace is None:
            return None
        if self._workspace_transport is None:
            raise ValueError("Fleet workspace transport is not configured")
        return await self._workspace_transport.snapshot(workspace)

    @staticmethod
    def _validate_result_identity(
        *,
        request: WorkRequest,
        result: OperatorResult,
    ) -> None:
        if (
            result.project_id != request.project_id
            or result.work_id != request.work_id
            or result.run_id != request.run_id
        ):
            raise ValueError("operator result belongs to a different request")

    @staticmethod
    def _validate_workspace_result_identity(
        snapshot: FleetWorkspaceTransfer, result: FleetWorkspaceTransferResult
    ) -> None:
        if (
            result.ref != snapshot.ref
            or result.project_id != snapshot.project_id
            or result.work_id != snapshot.work_id
            or result.kind != snapshot.kind
        ):
            raise ValueError("workspace result belongs to a different snapshot")

    async def run(
        self,
        request: WorkRequest,
        capsule: TaskCapsule,
        capabilities: CapabilitySet,
        workspace: Workspace | None,
    ) -> OperatorResult:
        required_capabilities = tuple(
            dict.fromkeys(
                (self._runtime_capability, *(grant.name for grant in capabilities.grants))
            )
        )
        snapshot = await self._snapshot(workspace)
        existing = await self._store.get_task(
            request.run_id,
            org_id=self._org_id,
            project_id=request.project_id,
        )
        if existing is None:
            selected = await self._select_worker(
                project_id=request.project_id,
                required_capabilities=required_capabilities,
            )
            payload = FleetOperatorTaskPayload(
                request=request,
                capsule=capsule,
                capabilities=self._transport_capabilities(capabilities),
                required_capabilities=required_capabilities,
                harness_tier=self._harness_tier,
                workspace=snapshot,
            )
            await self._store.enqueue(
                {
                    "run_id": request.run_id,
                    "org_id": self._org_id,
                    "project_id": request.project_id,
                    "pool": "default",
                    "labels": {
                        **capability_routing_labels(required_capabilities),
                        WORKER_ID_ROUTING_LABEL: selected.id,
                    },
                    "payload": payload.model_dump(mode="json"),
                }
            )

        while True:
            task = await self._store.get_task(
                request.run_id,
                org_id=self._org_id,
                project_id=request.project_id,
            )
            if task is None:
                raise RuntimeError("fleet task disappeared from canonical storage")
            task_input_digest = task.get("workspace_input_digest")
            if task["status"] != "completed" and (
                (snapshot is None) != (task_input_digest is None)
                or (snapshot is not None and task_input_digest != snapshot.input_digest)
            ):
                raise ValueError("canonical workspace changed after Fleet dispatch")
            if task["status"] == "failed":
                return _failed_result(
                    request, task.get("error") or "fleet worker failed"
                )
            if task["status"] == "pending":
                selected_worker_id = task.get("selected_worker_id")
                selected = await self._select_worker(
                    project_id=request.project_id,
                    required_capabilities=required_capabilities,
                    preferred_worker_id=selected_worker_id,
                )
                if selected.id != selected_worker_id:
                    await self._store.retarget_task(
                        request.run_id,
                        org_id=self._org_id,
                        project_id=request.project_id,
                        expected_worker_id=selected_worker_id,
                        selected_worker_id=selected.id,
                    )
            if task["status"] == "completed":
                output = task.get("output")
                if not output:
                    raise ValueError("fleet worker completed without an OperatorResult")
                envelope = FleetOperatorResultEnvelope.model_validate_json(output)
                result = envelope.result
                self._validate_result_identity(request=request, result=result)
                if snapshot is None:
                    if envelope.workspace_result is not None:
                        raise ValueError("workspace result returned for a workspace-free task")
                else:
                    if envelope.workspace_result is None:
                        raise ValueError("fleet worker completed without a workspace result")
                    if task_input_digest != envelope.workspace_result.input_digest:
                        raise ValueError(
                            "workspace result input does not match dispatched snapshot"
                        )
                    self._validate_workspace_result_identity(snapshot, envelope.workspace_result)
                    assert self._workspace_transport is not None
                    assert workspace is not None
                    await self._workspace_transport.apply(
                        workspace, snapshot, envelope.workspace_result
                    )
                if self._artifact_store is not None and envelope.activity_log is not None:
                    artifact = self._artifact_store.put_bytes(
                        envelope.activity_log.encode("utf-8"),
                        project_id=request.project_id,
                        media_type="application/x-ndjson",
                        created_by=self.name,
                    )
                    result = result.model_copy(
                        update={
                            "artifact_refs": (
                                *result.artifact_refs,
                                artifact.storage_ref,
                            )
                        }
                    )
                return result
            await asyncio.sleep(self._poll_interval_seconds)


def _failed_result(request: WorkRequest, summary: str) -> OperatorResult:
    return OperatorResult(
        project_id=request.project_id,
        work_id=request.work_id,
        run_id=request.run_id,
        status="failed",
        summary=summary[:4000],
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        profile_context={},
    )
