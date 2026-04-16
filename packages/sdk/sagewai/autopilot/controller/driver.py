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

This is a *stub* executor: no LLM calls are made. Each node is
"executed" by emitting a :class:`~sagewai.autopilot.controller.types.StepResult`
with ``output_preview="[stub] node_id completed"``. Branched graphs
log a warning and execute only the entry node.

The driver drives the :class:`~sagewai.autopilot.mission.Mission` state
machine:  SCHEDULED → RUNNING → COMPLETED (or FAILED on exception).
"""

from __future__ import annotations

import logging
import time

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.errors import MissionLifecycleError
from sagewai.autopilot.mission import Mission

from .types import MissionRunResult, StepResult

logger = logging.getLogger(__name__)


class MissionDriver:
    """Executes a scheduled mission against its agent graph (stub).

    Call :meth:`execute` with a :class:`Mission` that is in the
    SCHEDULED state.  The driver transitions the mission to RUNNING,
    walks the ``AgentGraph``, and finally transitions to COMPLETED or
    FAILED before returning a :class:`MissionRunResult`.

    No real agent execution takes place — every node produces a stub
    ``StepResult``.  This design makes the class fully testable without
    any LLM credentials or network access.
    """

    async def execute(self, mission: Mission) -> MissionRunResult:
        """Execute *mission* and return a :class:`MissionRunResult`.

        Args:
            mission: A mission in the SCHEDULED state.

        Returns:
            A frozen :class:`MissionRunResult` describing every step
            that was (stub-)executed and the final status.

        Raises:
            :class:`~sagewai.autopilot.errors.MissionLifecycleError`:
                If *mission* is not in the SCHEDULED state.
        """
        if mission.state is not MissionState.SCHEDULED:
            raise MissionLifecycleError(
                from_state=mission.state.value,
                to_state=MissionState.RUNNING.value,
            )

        mission.transition_to(MissionState.RUNNING)
        t0 = time.monotonic()
        steps: list[StepResult] = []

        try:
            steps = self._walk_graph(mission)
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

    def _walk_graph(self, mission: Mission) -> list[StepResult]:
        """Walk the mission's agent graph and return per-step results.

        For graphs with no branches, calls
        :meth:`~sagewai.autopilot.agent_graph.AgentGraph.traverse_linear`
        to get the ordered node list.  For graphs with branches, logs a
        warning and executes only the entry node as a stub.
        """
        from sagewai.autopilot.blueprint import Blueprint

        bp = Blueprint.model_validate_json(mission.slots["__blueprint_json__"])
        graph = bp.agent_graph

        if graph.branches:
            logger.warning(
                "Mission %s: agent graph has branches — stub executor "
                "cannot resolve conditional edges; executing entry node "
                "%r only.  Wire a real MissionDriver for branched graphs.",
                mission.mission_id,
                graph.entry,
            )
            return [self._stub_step(graph.entry)]

        node_order = graph.traverse_linear()
        return [self._stub_step(node_id) for node_id in node_order]

    def _stub_step(self, node_id: str) -> StepResult:
        return StepResult(
            node_id=node_id,
            status="completed",
            output_preview=f"[stub] {node_id} completed",
        )
