# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore
from sagewai.fleet.models import WorkerRecord
from sagewai.fleet.registry import InMemoryFleetRegistry

from .types import StepResult

if TYPE_CHECKING:
    from sagewai.autopilot.agent_graph import Agent
    from sagewai.autopilot.mission import Mission

# Optional type alias for the SSE event emitter injected by the admin run loop.
EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None] | None]

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
        event_emitter: EventEmitter | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._registry = registry
        self._poll_timeout = poll_timeout
        self._event_emitter = event_emitter

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
        from sagewai.autopilot.errors import NoWorkerAvailableError
        from sagewai.autopilot.controller.fleet_match import match_workers
        from sagewai.fleet.normalizer import ModelNormalizer

        run_id = str(uuid.uuid4())
        # Use mission.run_id as the Fleet job_id so post-mortem traces key off the same id.
        job_id = getattr(mission, "run_id", None) or run_id

        # ── Capability pre-check ─────────────────────────────────────
        # Only fail-fast when the pool has workers and none match.
        # An empty pool (e.g. single-tenant dev with no workers) falls
        # through to the claim loop (timeout → "skipped").
        pool_snapshot: list[WorkerRecord] = await self._snapshot_pool(mission)
        matched = match_workers(agent, pool_snapshot)

        if pool_snapshot and not matched:
            # Compute unmet capabilities for the diagnostic event.
            all_labels: set[str] = set()
            all_models: set[str] = set()
            for w in pool_snapshot:
                all_labels |= set(w.capabilities.labels.keys())
                all_models |= set(w.capabilities.models_canonical)

            required_tools = set(agent.tools) if agent.tools else set()
            raw_providers = [
                p.name if hasattr(p, "name") else p
                for p in (getattr(agent, "providers_required", None) or [])
            ]
            required_models = set(ModelNormalizer.canonical_list(raw_providers))

            unmet_labels = sorted(required_tools - all_labels)
            unmet_models = sorted(required_models - all_models)

            unmet_caps = {"labels": unmet_labels, "models_canonical": unmet_models}
            await self._emit("agent.no_worker_available", {
                "step_id": agent.id,
                "unmet_capabilities": unmet_caps,
            })

            raise NoWorkerAvailableError(
                agent.id,
                unmet_labels=unmet_labels,
                unmet_models=unmet_models,
            )

        eligible_ids = [w.id for w in matched]

        # ── Derive model tier from blueprint ─────────────────────────
        # Derive model tier from blueprint providers_required (first entry).
        model_tier = self._extract_model_tier(mission)

        # Extract sandbox requirements, consulting admin-state for overrides
        # (Plan 3b-i: level 2 admin override takes precedence over level 3 Blueprint).
        try:
            from sagewai.autopilot.blueprint import Blueprint as _Blueprint

            _bp = _Blueprint.model_validate_json(mission.slots.get("__blueprint_json__", "{}"))
        except Exception:
            _bp = None
        sandbox_fields = await self._extract_sandbox_requirements(_bp)

        task: dict = {
            "run_id": run_id,
            "job_id": job_id,
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
            # Sandbox requirements resolved via admin override → Blueprint cascade.
            # None means "use fleet worker defaults / cascade".
            **sandbox_fields,
        }

        # Enqueue the task into the store so the dispatcher can see it.
        await self._enqueue(task)

        await self._emit("agent.dispatched_to_worker", {
            "step_id": agent.id,
            "task_id": run_id,
            "queue_position": 0,
            "eligible_worker_ids": eligible_ids,
        })

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

        await self._emit("agent.worker_claimed", {
            "step_id": agent.id,
            "task_id": run_id,
            "worker_id": "autopilot-internal",
            "worker_name": "autopilot-internal",
        })

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

    async def _emit(self, event_name: str, payload: dict[str, Any]) -> None:
        """Call the injected event emitter if one was provided."""
        if self._event_emitter is None:
            return
        result = self._event_emitter(event_name, payload)
        if asyncio.iscoroutine(result):
            await result

    async def _snapshot_pool(self, mission: Mission) -> list[WorkerRecord]:
        """Return the current worker pool for capability pre-check.

        Uses the injected :class:`InMemoryFleetRegistry` to list workers.
        Falls back to an empty list on any error so the caller can surface
        a ``NoWorkerAvailableError`` with an empty ``matched`` list.
        """
        try:
            org_id = getattr(mission, "project_id", "default") or "default"
            return await self._registry.list_workers(org_id=org_id)
        except Exception:
            logger.debug("FleetMissionAdapter: could not snapshot worker pool")
            return []

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

    async def _extract_sandbox_requirements(
        self,
        blueprint: object,
    ) -> dict[str, str | None]:
        """Resolve sandbox requirements for a blueprint's entry agent.

        Plan 3b-i: admin-state override (level 2) takes precedence over
        blueprint-declared values (level 3) via resolve_agent_requirements().

        Returns a flat dict with keys ``requires_sandbox_mode``,
        ``requires_image``, and ``requires_network_policy``.  String values
        (or ``None``) are used so the task dict is JSON-serialisable.
        """
        from sagewai.sandbox.resolution import resolve_agent_requirements

        try:
            agent_name: str | None = None
            blueprint_reqs = None
            if blueprint is not None:
                agent_name = getattr(
                    getattr(blueprint, "agent_graph", None), "entry", None
                )
                blueprint_reqs = getattr(blueprint, "sandbox_requirements", None)

            if agent_name is None:
                reqs = blueprint_reqs
            else:
                reqs = await resolve_agent_requirements(
                    agent_name,
                    blueprint_requirements=blueprint_reqs,
                )
        except Exception:
            logger.debug(
                "FleetMissionAdapter: could not resolve sandbox requirements from blueprint",
            )
            reqs = None

        if reqs is None:
            return {
                "requires_sandbox_mode": None,
                "requires_image": None,
                "requires_network_policy": None,
            }
        return {
            "requires_sandbox_mode": reqs.sandbox_mode.value,
            "requires_image": reqs.image,
            "requires_network_policy": reqs.network_policy.value,
        }

    async def _enqueue(self, task: dict) -> None:
        """Enqueue a task into the dispatcher's backing store.

        Works when the dispatcher's ``_store`` is an
        :class:`InMemoryTaskStore`.  For other store implementations this
        is a no-op (the caller is expected to have already enqueued the
        task externally).
        """
        store = self._dispatcher._store  # noqa: SLF001
        if isinstance(store, InMemoryTaskStore):
            await store.enqueue(task)
        else:
            logger.debug(
                "FleetMissionAdapter._enqueue: store is not InMemoryTaskStore; "
                "skipping auto-enqueue — caller must enqueue externally.",
            )
