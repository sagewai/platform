# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""EvalHarness — CI-friendly golden-goal test runner for the autopilot framework.

The harness validates :class:`GoalRouter` against a :class:`GoldenGoalSet`
using a caller-supplied (mocked) :class:`SagewaiLLMClient`. It produces a
frozen :class:`EvalReport` with top-1 accuracy, band accuracy, and
false-positive rate.

Design notes:

- The harness is async internally (it calls ``GoalRouter.route()`` which is
  ``async``). The public :meth:`EvalHarness.run` method wraps the async core
  in ``asyncio.run()`` for ease of use in synchronous test code and CLI
  scripts.
- Each goal is evaluated independently. The harness never shares router state
  between goals.
- A *false positive* is defined as a synthesis-band goal where the router
  returned a non-synthesis result (``AutoRouted`` or ``PickerNeeded``). This
  is the strictest metric because false positives cause silent misrouting in
  production.
- Picker-band goals contribute to ``band_accuracy`` and optionally to
  ``top1_accuracy`` when the top picker candidate matches the expected ID.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from sagewai.autopilot.routing import (
    AutoRouted,
    ConfidenceConfig,
    GoalRouter,
    PickerNeeded,
    RoutingResult,
    SynthesisNeeded,
)

from .types import EvalConfig, EvalReport, GoldenGoalSet

if TYPE_CHECKING:
    from sagewai.autopilot.sagewai_llm import SagewaiLLMClient


def _band_of(result: RoutingResult) -> str:
    """Map a RoutingResult to the corresponding GoldenGoal band string."""
    if isinstance(result, AutoRouted):
        return "auto_route"
    if isinstance(result, PickerNeeded):
        return "picker"
    return "synthesis"


def _top1_id_of(result: RoutingResult) -> str | None:
    """Extract the top-1 blueprint ID from a result, or None for synthesis."""
    if isinstance(result, AutoRouted):
        try:
            return json.loads(result.ranked.blueprint_json).get("id")
        except (ValueError, KeyError):
            return None
    if isinstance(result, PickerNeeded) and result.candidates:
        try:
            return json.loads(result.candidates[0].blueprint_json).get("id")
        except (ValueError, KeyError):
            return None
    return None


class EvalHarness:
    """Run a :class:`GoldenGoalSet` through :class:`GoalRouter` and report metrics.

    Args:
        goal_set: The golden goals to evaluate.
        client: A :class:`SagewaiLLMClient` instance (typically an
            ``AsyncMock`` in test contexts).
        config: Confidence thresholds to use.  Defaults to production values.
    """

    def __init__(
        self,
        *,
        goal_set: GoldenGoalSet,
        client: SagewaiLLMClient,
        config: EvalConfig | None = None,
    ) -> None:
        self._goal_set = goal_set
        self._client = client
        self.config: EvalConfig = config or EvalConfig()

    def _make_router(self) -> GoalRouter:
        """Construct a GoalRouter from the harness client and config."""
        confidence = ConfidenceConfig(
            auto_route_threshold=self.config.auto_route_threshold,
            picker_threshold=self.config.picker_threshold,
            picker_top_k=self.config.retrieve_k,
        )
        return GoalRouter(
            client=self._client,
            config=confidence,
            retrieve_k=self.config.retrieve_k,
        )

    async def _route_goal(self, goal: str) -> RoutingResult:
        """Route a single goal string. Separated for easy patching in tests."""
        router = self._make_router()
        return await router.route(goal)

    async def _run_async(self) -> EvalReport:
        """Async core: evaluate every goal and compute metrics."""
        goals = self._goal_set.goals
        total = len(goals)
        top1_correct = 0
        band_correct = 0
        synthesis_goals = 0
        false_positives = 0

        start = time.perf_counter()
        for gg in goals:
            result = await self._route_goal(gg.goal)
            actual_band = _band_of(result)
            actual_id = _top1_id_of(result)

            # Band accuracy
            if actual_band == gg.expected_band:
                band_correct += 1

            # Top-1 accuracy
            if gg.expected_blueprint_id is None:
                # Synthesis goal: correct when router also returns synthesis
                if isinstance(result, SynthesisNeeded):
                    top1_correct += 1
            else:
                if actual_id == gg.expected_blueprint_id:
                    top1_correct += 1

            # False-positive counting (synthesis expected, non-synthesis returned)
            if gg.expected_band == "synthesis":
                synthesis_goals += 1
                if not isinstance(result, SynthesisNeeded):
                    false_positives += 1

        elapsed = time.perf_counter() - start
        fp_rate = false_positives / synthesis_goals if synthesis_goals else 0.0

        return EvalReport(
            total_goals=total,
            top1_accuracy=top1_correct / total,
            band_accuracy=band_correct / total,
            false_positive_rate=fp_rate,
            duration_seconds=elapsed,
        )

    def run(self) -> EvalReport:
        """Synchronous entry point — runs the async core via ``asyncio.run()``."""
        return asyncio.run(self._run_async())


def run_eval(
    *,
    goal_set: GoldenGoalSet,
    client: SagewaiLLMClient,
    config: EvalConfig | None = None,
) -> EvalReport:
    """Convenience wrapper: construct a harness and run it in one call.

    Args:
        goal_set: The golden goals to evaluate.
        client: A :class:`SagewaiLLMClient` instance (typically mocked).
        config: Optional custom confidence thresholds.

    Returns:
        A frozen :class:`EvalReport`.
    """
    return EvalHarness(goal_set=goal_set, client=client, config=config).run()
