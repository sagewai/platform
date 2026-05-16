# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HealingEngine — pure recommendation engine for autopilot self-healing ops.

The engine is a thin orchestration layer on top of :class:`HealthMonitor`.
It accepts a list of :class:`~sagewai.autopilot.controller.MissionRunResult`
records, groups them by blueprint, runs all four detection rules, and
translates each :class:`~sagewai.autopilot.healing.monitor.HealthSignal`
into one or more typed :data:`~sagewai.autopilot.healing.types.HealingAction`
recommendations.

Signal → action mapping
-----------------------
- ``provider_failure``  → :class:`RotateProvider`
- ``cost_spike``        → :class:`PauseBudget` + :class:`AlertOperator` (warning)
- ``drift``             → :class:`AlertOperator` (critical)
- ``timeout``           → :class:`RetryMission` + :class:`AlertOperator` (warning)

The returned list is deduplicated: if the same (kind, mission_id /
blueprint_id) combination would be emitted more than once, only the
first occurrence is kept.
"""

from __future__ import annotations

from sagewai.autopilot.controller.types import MissionRunResult

from .monitor import HealthMonitor, HealthSignal
from .types import (
    AlertOperator,
    HealingAction,
    HealingPolicy,
    PauseBudget,
    RetryMission,
    RotateProvider,
)

# ---------------------------------------------------------------------------
# Context passed alongside mission results so the engine has enough data
# to run cost and timeout checks.
# ---------------------------------------------------------------------------


class MissionContext:
    """Optional per-mission metadata needed for cost and timeout detection.

    Parameters:
        blueprint_id: Blueprint that produced this mission.
        estimated_cost: Budgeted cost from the blueprint (USD).
        actual_cost: Observed cost at mission completion (USD).
        estimated_duration: Expected wall-clock duration from the
            blueprint (seconds).
    """

    def __init__(
        self,
        blueprint_id: str,
        estimated_cost: float = 0.0,
        actual_cost: float = 0.0,
        estimated_duration: float = 0.0,
    ) -> None:
        self.blueprint_id = blueprint_id
        self.estimated_cost = estimated_cost
        self.actual_cost = actual_cost
        self.estimated_duration = estimated_duration


class HealingEngine:
    """Pure recommendation engine for autopilot self-healing ops.

    Parameters:
        monitor: A :class:`HealthMonitor` instance to accumulate state into.
            Callers may share a monitor across multiple engine invocations
            to maintain rolling state.
        policy: Optional :class:`HealingPolicy` override. When omitted the
            engine reads the policy from the injected *monitor*.
    """

    def __init__(
        self,
        monitor: HealthMonitor,
        policy: HealingPolicy | None = None,
    ) -> None:
        self._monitor = monitor
        self._policy = policy or monitor._policy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        results: list[MissionRunResult],
        contexts: list[MissionContext] | None = None,
    ) -> list[HealingAction]:
        """Evaluate *results* and return a list of recommended actions.

        Parameters:
            results: Mission run results to analyse. Each result is ingested
                into the monitor before signals are collected.
            contexts: Optional per-result metadata for cost and timeout
                detection. Must be the same length as *results* if provided.
                Results without a matching context skip cost/timeout checks.

        Returns:
            A deduplicated, ordered list of :data:`HealingAction` recommendations.
            Returns an empty list when no issues are detected.
        """
        if contexts is not None and len(contexts) != len(results):
            raise ValueError(
                f"contexts length ({len(contexts)}) must match " f"results length ({len(results)})"
            )

        actions: list[HealingAction] = []
        seen: set[str] = set()  # dedup keys

        for idx, result in enumerate(results):
            ctx = contexts[idx] if contexts else None
            blueprint_id = ctx.blueprint_id if ctx else result.mission_id

            # Ingest into the monitor (updates consecutive counters + window)
            self._monitor.ingest_for_blueprint(blueprint_id, result)

            # 1. Provider failure detection
            for signal in self._monitor.detect_provider_failures(blueprint_id):
                action = RotateProvider(blueprint_id=blueprint_id)
                key = f"rotate_provider:{blueprint_id}"
                if key not in seen:
                    seen.add(key)
                    actions.append(action)

            # 2. Cost spike detection (requires context)
            if ctx and ctx.estimated_cost > 0:
                for signal in self._monitor.detect_cost_spike(
                    estimated_cost=ctx.estimated_cost,
                    actual_cost=ctx.actual_cost,
                    mission_id=result.mission_id,
                    blueprint_id=blueprint_id,
                ):
                    self._emit_cost_spike(actions, seen, result.mission_id, signal)

            # 3. Drift detection (uses sliding window accumulated so far)
            for signal in self._monitor.detect_drift(blueprint_id):
                key = f"alert_operator:drift:{blueprint_id}"
                if key not in seen:
                    seen.add(key)
                    actions.append(
                        AlertOperator(
                            message=(
                                f"Success rate drift detected for blueprint "
                                f"'{blueprint_id}': {signal.detail}"
                            ),
                            severity="critical",
                        )
                    )

            # 4. Timeout detection (requires context)
            if ctx and ctx.estimated_duration > 0:
                for signal in self._monitor.detect_timeout(
                    estimated_duration=ctx.estimated_duration,
                    actual_duration=result.duration_seconds,
                    mission_id=result.mission_id,
                    blueprint_id=blueprint_id,
                ):
                    self._emit_timeout(actions, seen, result.mission_id, signal)

        return actions

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit_cost_spike(
        self,
        actions: list[HealingAction],
        seen: set[str],
        mission_id: str,
        signal: HealthSignal,
    ) -> None:
        pause_key = f"pause_budget:{mission_id}"
        if pause_key not in seen:
            seen.add(pause_key)
            actions.append(
                PauseBudget(
                    mission_id=mission_id,
                    reason=f"Cost spike detected: {signal.detail}",
                )
            )
        alert_key = f"alert_operator:cost_spike:{mission_id}"
        if alert_key not in seen:
            seen.add(alert_key)
            actions.append(
                AlertOperator(
                    message=f"Cost spike on mission '{mission_id}': {signal.detail}",
                    severity="warning",
                )
            )

    def _emit_timeout(
        self,
        actions: list[HealingAction],
        seen: set[str],
        mission_id: str,
        signal: HealthSignal,
    ) -> None:
        retry_key = f"retry_mission:{mission_id}"
        if retry_key not in seen:
            seen.add(retry_key)
            actions.append(
                RetryMission(
                    mission_id=mission_id,
                    backoff_seconds=30.0,
                )
            )
        alert_key = f"alert_operator:timeout:{mission_id}"
        if alert_key not in seen:
            seen.add(alert_key)
            actions.append(
                AlertOperator(
                    message=f"Timeout on mission '{mission_id}': {signal.detail}",
                    severity="warning",
                )
            )
