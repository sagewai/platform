# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MissionDriver — executes a scheduled mission by walking its AgentGraph.

Each node is executed by :class:`~sagewai.autopilot.controller.executor.AgentExecutor`
which delegates to LiteLLM for LLM nodes and short-circuits for
deterministic nodes.  When no LLM provider is configured the executor
returns a "skipped" result rather than raising.  Branched graphs log a
warning and execute only the entry node.

The driver drives the :class:`~sagewai.autopilot.mission.Mission` state
machine:  SCHEDULED → RUNNING → COMPLETED (or FAILED on exception).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.errors import MissionLifecycleError
from sagewai.autopilot.mission import Mission

from .executor import AgentExecutor, ExecutorConfig
from .types import MissionRunResult, StepResult

# Optional integrations — imported lazily to avoid circular deps
# when fleet or scheduler are not used.
FleetMissionAdapter = None  # set below if fleet_adapter module exists
MissionScheduler = None  # set below if scheduler module exists

logger = logging.getLogger(__name__)


class MissionDriver:
    """Executes a scheduled mission against its agent graph via AgentExecutor.

    Call :meth:`execute` with a :class:`Mission` that is in the
    SCHEDULED state.  The driver transitions the mission to RUNNING,
    walks the ``AgentGraph`` calling :class:`AgentExecutor` for each
    node, and finally transitions to COMPLETED or FAILED before
    returning a :class:`MissionRunResult`.

    v1.1 compositional blueprints carry a ``composition`` list instead of
    a concrete ``agent_graph``.  Pass ``resolver`` so the driver can
    materialise the composition before walking the graph.  The resolver
    signature is ``(composition: list) -> AgentGraph | dict``; returning a
    plain dict is accepted and automatically wrapped in an AgentGraph.

    Args:
        executor_config: Optional :class:`ExecutorConfig` forwarded to
            :class:`AgentExecutor`.  Defaults are used if not given.
        blueprint: Optional pre-loaded blueprint (for preview/test contexts
            that call :meth:`_materialise_if_needed` directly).
        resolver: Optional callable that materialises a composition list
            into an agent graph.  Required for v1.1 blueprints.
    """

    def __init__(
        self,
        executor_config: ExecutorConfig | None = None,
        fleet_adapter: object | None = None,
        scheduler: object | None = None,
        blueprint: Optional[object] = None,
        resolver: Optional[Callable] = None,
    ) -> None:
        self._executor = AgentExecutor(executor_config)
        self._fleet_adapter = fleet_adapter
        self._scheduler = scheduler
        self.blueprint = blueprint
        self.resolver = resolver

    def _materialise_if_needed(self, bp: Optional[object] = None) -> object:
        """Materialise a compositional blueprint's ``composition`` into ``agent_graph``.

        If *bp* is given, operates on it; otherwise falls back to
        ``self.blueprint``.  For v1 blueprints (``agent_graph`` already set)
        this is a no-op.  Updates ``self.blueprint`` in-place (via
        :meth:`~pydantic.BaseModel.model_copy`) when the stored blueprint is
        used.

        Returns the (possibly updated) blueprint.
        """
        from sagewai.autopilot.agent_graph import AgentGraph
        from sagewai.autopilot.blueprint import Blueprint

        target: Optional[Blueprint] = bp if bp is not None else self.blueprint  # type: ignore[assignment]
        if target is None:
            raise RuntimeError("No blueprint available for materialisation.")
        if target.agent_graph is not None:
            return target
        if not target.composition:
            raise RuntimeError(
                "Blueprint has neither agent_graph nor composition (validator should have caught this)."
            )
        if self.resolver is None:
            raise RuntimeError(
                "Compositional blueprint requires a resolver; pass MissionDriver(resolver=...)."
            )
        resolved = self.resolver(target.composition)
        if isinstance(resolved, dict):
            resolved = AgentGraph.model_validate(resolved)
        new_bp = target.model_copy(update={"agent_graph": resolved})
        if bp is None:
            self.blueprint = new_bp
        return new_bp

    async def execute(self, mission: Mission) -> MissionRunResult:
        """Execute *mission* and return a :class:`MissionRunResult`.

        Args:
            mission: A mission in the SCHEDULED state.

        Returns:
            A frozen :class:`MissionRunResult` describing every step
            executed and the final status.

        Raises:
            :class:`~sagewai.autopilot.errors.MissionLifecycleError`:
                If *mission* is not in the SCHEDULED state.
        """
        if mission.state is not MissionState.SCHEDULED:
            raise MissionLifecycleError(
                from_state=mission.state.value,
                to_state=MissionState.RUNNING.value,
            )

        # If a scheduler is provided and blueprint mode is "scheduled",
        # defer execution — the scheduler's tick() will call us back.
        if self._scheduler is not None:
            from sagewai.autopilot.blueprint import Blueprint

            bp = Blueprint.model_validate_json(mission.slots.get("__blueprint_json__", "{}"))
            if bp.mode is not None and bp.mode.value == "scheduled" and hasattr(self._scheduler, "schedule"):
                self._scheduler.schedule(mission)
                logger.info(
                    "Mission %s deferred to scheduler (mode=scheduled)",
                    mission.mission_id,
                )
                return MissionRunResult(
                    mission_id=mission.mission_id,
                    status="deferred",
                    steps=(),
                    duration_seconds=0.0,
                    error=None,
                )

        mission.transition_to(MissionState.RUNNING)
        t0 = time.monotonic()
        steps: list[StepResult] = []

        try:
            steps = await self._walk_graph(mission)
        except Exception as exc:  # noqa: BLE001
            duration = time.monotonic() - t0
            mission.transition_to(MissionState.FAILED)
            logger.error(
                "Mission %s failed during graph walk: %s",
                mission.mission_id,
                exc,
            )
            return MissionRunResult(
                mission_id=mission.mission_id,
                status="failed",
                steps=tuple(steps),
                duration_seconds=duration,
                error=str(exc),
            )

        duration = time.monotonic() - t0
        mission.transition_to(MissionState.COMPLETED)
        logger.info(
            "Mission %s completed in %.3fs (%d steps)",
            mission.mission_id,
            duration,
            len(steps),
        )
        return MissionRunResult(
            mission_id=mission.mission_id,
            status="completed",
            steps=tuple(steps),
            duration_seconds=duration,
            error=None,
        )

    # ── private helpers ─────────────────────────────────────────────

    async def _walk_graph(self, mission: Mission) -> list[StepResult]:
        """Walk the mission's agent graph and return per-step results.

        For graphs with no branches, calls
        :meth:`~sagewai.autopilot.agent_graph.AgentGraph.traverse_linear`
        to get the ordered node list.  For graphs with branches, logs a
        warning and executes only the entry node.

        An accumulating context dict is passed between steps — each
        step's ``output_preview`` is merged into the context under the
        key ``"step_{node_id}_output"`` so subsequent nodes can see
        prior results.
        """
        from sagewai.autopilot.blueprint import Blueprint

        bp = Blueprint.model_validate_json(mission.slots["__blueprint_json__"])
        bp = self._materialise_if_needed(bp)  # no-op for v1; resolves composition for v1.1
        graph = bp.agent_graph

        # Build a mutable context from mission slots (excluding internals)
        context: dict = {k: v for k, v in mission.slots.items() if not k.startswith("__")}

        # Build a lookup from node_id → Agent
        nodes_by_id = {agent.id: agent for agent in graph.nodes}

        if graph.branches:
            logger.warning(
                "Mission %s: agent graph has branches — executor "
                "cannot resolve conditional edges; executing entry node "
                "%r only.",
                mission.mission_id,
                graph.entry,
            )
            entry_agent = nodes_by_id[graph.entry]
            step = await self._execute_node(entry_agent, mission, context)
            return [step]

        node_order = graph.traverse_linear()
        results: list[StepResult] = []
        for node_id in node_order:
            agent = nodes_by_id[node_id]
            step = await self._execute_node(agent, mission, context)
            results.append(step)
            # Accumulate output into context for next step
            if step.output_preview:
                context[f"step_{node_id}_output"] = step.output_preview
        return results

    async def _execute_node(self, agent, mission, context: dict) -> StepResult:
        """Execute one agent node, catching any unexpected errors.

        If a fleet adapter is configured, delegates to it for dispatch.
        Otherwise uses the local AgentExecutor.
        """
        try:
            if self._fleet_adapter is not None and hasattr(self._fleet_adapter, "dispatch_step"):
                return await self._fleet_adapter.dispatch_step(agent, mission, context)
            return await self._executor.execute(agent, context)
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error executing agent %r: %s", agent.id, exc)
            return StepResult(
                node_id=agent.id,
                status="failed",
                output_preview=str(exc)[:200],
            )
