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

from sagewai.fleet.dispatcher import TaskStore
from sagewai.fleet.models import capability_routing_labels
from sagewai.work.models import TaskCapsule
from sagewai.work.runtime import (
    CapabilitySet,
    OperatorResult,
    WorkRequest,
    Workspace,
)


class FleetOperatorRuntime:
    """Dispatch one Work stage to a capability-matched Fleet worker."""

    def __init__(
        self,
        *,
        store: TaskStore,
        org_id: str,
        runtime_capability: str,
        poll_interval_seconds: float,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._store = store
        self._org_id = org_id
        self._runtime_capability = runtime_capability
        self._poll_interval_seconds = poll_interval_seconds
        self.name = f"fleet:{runtime_capability}"

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
        existing = await self._store.get_task(
            request.run_id,
            org_id=self._org_id,
            project_id=request.project_id,
        )
        if existing is None:
            await self._store.enqueue(
                {
                    "run_id": request.run_id,
                    "org_id": self._org_id,
                    "project_id": request.project_id,
                    "pool": "default",
                    "labels": capability_routing_labels(required_capabilities),
                    "payload": {
                        "kind": "work.operator",
                        "request": request.model_dump(mode="json"),
                        "capsule": capsule.model_dump(mode="json"),
                        "capabilities": capabilities.model_dump(mode="json"),
                        "required_capabilities": list(required_capabilities),
                        "workspace_ref": workspace.ref if workspace is not None else None,
                    },
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
            if task["status"] == "failed":
                raise RuntimeError(task.get("error") or "fleet worker failed")
            if task["status"] == "completed":
                output = task.get("output")
                if not output:
                    raise ValueError("fleet worker completed without an OperatorResult")
                result = OperatorResult.model_validate_json(output)
                if (
                    result.project_id != request.project_id
                    or result.work_id != request.work_id
                    or result.run_id != request.run_id
                ):
                    raise ValueError("operator result belongs to a different request")
                return result
            await asyncio.sleep(self._poll_interval_seconds)
