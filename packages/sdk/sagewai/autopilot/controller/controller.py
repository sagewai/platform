# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""AutopilotController — top-level orchestrator for the autopilot framework.

Wires together:

1. A goal router — maps a plain-English goal to a RoutingResult.
2. :class:`~sagewai.autopilot.mission.Mission` creation — when the
   router returns :class:`~sagewai.autopilot.routing.AutoRouted`, binds
   the blueprint and extracted slots into a mission and transitions it
   to APPROVED.
3. :class:`MissionDriver` — executes the scheduled mission by stub-walking
   the agent graph.

The controller is intentionally *thin*: it delegates all domain logic to
the classes it composes.  Its role is to enforce the lifecycle sequence:

    route → create → APPROVED → SCHEDULED → RUNNING → COMPLETED/FAILED
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.autopilot._types import MissionState
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.errors import MissionLifecycleError
from sagewai.autopilot.mission import Mission
from sagewai.autopilot.routing.types import AutoRouted, RoutingResult

from .driver import MissionDriver
from .types import ControllerConfig, MissionRunResult

logger = logging.getLogger(__name__)


class AutopilotController:
    """Top-level autopilot orchestrator.

    Args:
        router: A configured goal router.
        client: A connected Sagewai LLM client.
        config: Controller configuration.  Defaults to
            :class:`ControllerConfig` with ``project_id="default"``.
        driver: The mission executor.  Defaults to a new
            :class:`MissionDriver`.
    """

    def __init__(
        self,
        *,
        router: Any,
        client: Any,
        config: ControllerConfig | None = None,
        driver: MissionDriver | None = None,
    ) -> None:
        self._router = router
        self._client = client
        self.config = config or ControllerConfig()
        self.driver = driver or MissionDriver()

    async def start_mission(self, goal: str) -> RoutingResult:
        """Route *goal* and, if auto-routed, create an APPROVED mission.

        When the router returns :class:`AutoRouted`, this method creates
        a :class:`Mission` from the blueprint and transitions it to APPROVED.
        The ``AutoRouted`` result object is extended in-place with a
        ``.mission`` attribute (via ``object.__setattr__`` to bypass
        Pydantic's frozen guard) so the caller can inspect it without a
        separate lookup.

        For ``PickerNeeded`` and ``SynthesisNeeded`` outcomes the result
        is returned unchanged — no mission is created.

        Args:
            goal: Plain-English description of the desired mission.

        Returns:
            The :class:`RoutingResult` from the router.  If auto-routed,
            the result carries a ``.mission`` attribute (type
            :class:`Mission`) in state APPROVED.
        """
        result = await self._router.route(goal)

        if isinstance(result, AutoRouted):
            mission = self._create_mission(result)
            mission.transition_to(MissionState.APPROVED)
            # AutoRouted is frozen Pydantic — use object.__setattr__ to
            # inject the mission reference without triggering validation.
            object.__setattr__(result, "mission", mission)
            logger.info(
                "Mission %s created for goal %r (blueprint %s)",
                mission.mission_id,
                goal[:80],
                mission.blueprint_id,
            )

        return result

    async def approve_and_schedule(self, mission: Mission) -> Mission:
        """Transition an APPROVED mission to SCHEDULED.

        Args:
            mission: A mission in the APPROVED state.

        Returns:
            The same mission object, now in the SCHEDULED state.

        Raises:
            :class:`~sagewai.autopilot.errors.MissionLifecycleError`:
                If *mission* is not in APPROVED state.
        """
        if mission.state is not MissionState.APPROVED:
            raise MissionLifecycleError(
                from_state=mission.state.value,
                to_state=MissionState.SCHEDULED.value,
            )
        mission.transition_to(MissionState.SCHEDULED)
        logger.info("Mission %s scheduled.", mission.mission_id)
        return mission

    async def run_mission(self, mission: Mission) -> MissionRunResult:
        """Execute a SCHEDULED mission by delegating to :class:`MissionDriver`.

        Args:
            mission: A mission in the SCHEDULED state.

        Returns:
            A :class:`MissionRunResult` describing the execution.

        Raises:
            :class:`~sagewai.autopilot.errors.MissionLifecycleError`:
                If *mission* is not in SCHEDULED state.
        """
        return await self.driver.execute(mission)

    # ── private helpers ─────────────────────────────────────────────

    def _create_mission(self, result: AutoRouted) -> Mission:
        """Bind an AutoRouted result into a draft Mission."""
        blueprint = Blueprint.model_validate_json(result.ranked.blueprint_json)

        # Merge default_slots from config under extracted slots.
        # __blueprint_json__ is injected after validate_slots (which rejects unknown keys).
        slots: dict[str, Any] = dict(self.config.default_slots)
        slots.update(result.slots)

        mission = Mission.from_blueprint(
            blueprint,
            project_id=self.config.project_id,
            slots=slots,
            registry=self.config.registry,
        )
        # Inject blueprint JSON after validation so MissionDriver can load the graph.
        mission.slots["__blueprint_json__"] = result.ranked.blueprint_json
        return mission
