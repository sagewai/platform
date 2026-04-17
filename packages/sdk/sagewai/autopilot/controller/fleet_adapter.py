# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""FleetMissionAdapter — bridges autopilot missions to fleet task dispatch.

Translates an autopilot :class:`~sagewai.autopilot.mission.Mission` step into
a fleet :class:`~sagewai.fleet.dispatcher.InMemoryTaskStore` task and drives
the claim/report round-trip through :class:`~sagewai.fleet.dispatcher.FleetDispatcher`.

Project isolation is enforced via the worker ``project_id`` label: only a
worker whose labels include ``{"project_id": mission.project_id}`` can claim
an autopilot task.

Usage::

    from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
    from sagewai.fleet.registry import InMemoryFleetRegistry
    from sagewai.autopilot.controller.fleet_adapter import FleetMissionAdapter
    from sagewai.autopilot.controller.driver import MissionDriver

    store = InMemoryTaskStore()
    dispatcher = FleetDispatcher(store=store, poll_timeout=5.0)
    registry = InMemoryFleetRegistry()

    adapter = FleetMissionAdapter(dispatcher=dispatcher, registry=registry)
    driver = MissionDriver(fleet_adapter=adapter)
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.registry import InMemoryFleetRegistry

from .types import StepResult

if TYPE_CHECKING:
    from sagewai.autopilot.agent_graph import Agent
    from sagewai.autopilot.mission import Mission

logger = logging.getLogger(__name__)


class FleetMissionAdapter:
    """Bridges autopilot mission steps to fleet task dispatch.

    For each step, the adapter:

    1. Derives the task ``model`` from the first ``ProviderRequirement``
       tier on the blueprint (e.g. ``"medium"``).
    2. Sets ``pool`` to ``mission.project_id`` for project-scoped routing.
    3. Attaches labels ``{"autopilot": "true", "blueprint_id": ...}`` so
       workers can filter autopilot tasks.
    4. Enqueues the task in the supplied :class:`InMemoryTaskStore` (or any
       compatible store backing the dispatcher).
    5. Simulates a worker claim via :meth:`FleetDispatcher.claim` and a
       subsequent :meth:`FleetDispatcher.report` call to complete the round-trip.

    Project isolation: only workers registered with
    ``labels={"project_id": mission.project_id}`` can claim the task
    because ``pool`` is set to ``mission.project_id``.  The
    :class:`~sagewai.fleet.dispatcher.InMemoryTaskStore` enforces pool
    matching at claim time.

    Args:
        dispatcher: The :class:`FleetDispatcher` to dispatch tasks through.
        registry: The :class:`InMemoryFleetRegistry` holding registered
            workers (used for logging / future capacity checks).
        poll_timeout: Maximum seconds to wait for a worker claim before
            returning a ``"skipped"`` result.  Defaults to ``5.0`` so tests
            do not hang.
    """

    def __init__(
        self,
        dispatcher: FleetDispatcher,
        registry: InMemoryFleetRegistry,
        poll_timeout: float = 5.0,
    ) -> None:
        self._dispatcher = dispatcher
        self._registry = registry
        self._poll_timeout = poll_timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def dispatch_step(
        self,
        agent: Agent,
        mission: Mission,
        context: dict,
    ) -> StepResult:
        """Dispatch one agent-graph node as a fleet task.

        The task is enqueued and then claimed synchronously within this
        call (using the underlying :class:`FleetDispatcher`).  If no
        worker claims the task before ``poll_timeout`` expires, the step
        is returned as ``"skipped"``.

        Args:
            agent: The agent node being executed (provides the node id).
            mission: The running mission (provides project_id, blueprint_id).
            context: Arbitrary caller context (forwarded as task payload).

        Returns:
            A :class:`StepResult` with ``status="completed"`` on success
            or ``status="skipped"`` when no worker is available.
        """
        run_id = str(uuid.uuid4())

        # Derive model tier from blueprint providers_required (first entry).
        model_tier = self._extract_model_tier(mission)

        task: dict = {
            "run_id": run_id,
            "model": model_tier,
            "pool": mission.project_id,
            "labels": {
                "autopilot": "true",
                "blueprint_id": mission.blueprint_id,
                "project_id": mission.project_id,
            },
            "payload": {
                "node_id": agent.id,
                "mission_id": mission.mission_id,
                "context": context,
            },
        }

        # Enqueue the task into the store so the dispatcher can see it.
        self._enqueue(task)

        logger.debug(
            "FleetMissionAdapter: dispatching node %r for mission %s (pool=%s, model=%s)",
            agent.id,
            mission.mission_id,
            mission.project_id,
            model_tier,
        )

        # Attempt to claim.  The dispatcher will poll for up to poll_timeout.
        claimed = await self._dispatcher.claim(
            worker_id="autopilot-internal",
            org_id="autopilot",
            models_canonical=[model_tier],
            pool=mission.project_id,
            labels={
                "project_id": mission.project_id,
                "autopilot": "true",
                "blueprint_id": mission.blueprint_id,
            },
        )

        if claimed is None:
            logger.warning(
                "FleetMissionAdapter: no worker available for node %r (mission %s)",
                agent.id,
                mission.mission_id,
            )
            return StepResult(
                node_id=agent.id,
                status="skipped",
                output_preview="No fleet worker available",
            )

        # Simulate worker completing the task.
        output_preview = f"[fleet] {agent.id} completed by worker on pool={mission.project_id}"
        await self._dispatcher.report(
            worker_id="autopilot-internal",
            org_id="autopilot",
            run_id=run_id,
            status="completed",
            output=output_preview,
        )

        logger.debug(
            "FleetMissionAdapter: node %r completed (run_id=%s)",
            agent.id,
            run_id,
        )
        return StepResult(
            node_id=agent.id,
            status="completed",
            output_preview=output_preview,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_model_tier(self, mission: Mission) -> str:
        """Derive a model tier string from the blueprint's first provider requirement.

        Falls back to ``"default"`` when the blueprint cannot be parsed or
        has no provider requirements.
        """
        try:
            from sagewai.autopilot.blueprint import Blueprint

            bp = Blueprint.model_validate_json(mission.slots.get("__blueprint_json__", "{}"))
            if bp.providers_required:
                return bp.providers_required[0].tier
        except Exception:
            logger.debug(
                "FleetMissionAdapter: could not extract model tier from blueprint; using 'default'",
            )
        return "default"

    def _enqueue(self, task: dict) -> None:
        """Enqueue a task into the dispatcher's backing store.

        Works when the dispatcher's ``_store`` is an
        :class:`InMemoryTaskStore`.  For other store implementations this
        is a no-op (the caller is expected to have already enqueued the
        task externally).
        """
        store = self._dispatcher._store  # noqa: SLF001
        if isinstance(store, InMemoryTaskStore):
            store.enqueue(task)
        else:
            logger.debug(
                "FleetMissionAdapter._enqueue: store is not InMemoryTaskStore; "
                "skipping auto-enqueue — caller must enqueue externally.",
            )
