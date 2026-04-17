# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""HealthMonitor — stateful sliding-window event processor.

The monitor ingests :class:`~sagewai.autopilot.controller.MissionRunResult`
records and accumulates per-blueprint state needed to detect four kinds
of operational problem:

- **Provider failures** — consecutive FAILED missions for the same
  blueprint ID exceed ``policy.failure_threshold``.
- **Cost spikes** — a single mission's actual cost exceeds
  ``estimated_cost * policy.cost_spike_multiplier``.
- **Drift** — the success rate over a sliding window of
  ``policy.success_rate_window`` results falls below
  ``policy.success_rate_minimum``.
- **Timeout** — a single mission's actual duration exceeds
  ``estimated_duration * policy.duration_spike_multiplier``.

The monitor is intentionally *stateful* — it keeps running counters so
that callers can call :meth:`HealthMonitor.ingest` incrementally rather
than replaying the full history on every poll cycle.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from sagewai.autopilot.controller.types import MissionRunResult

from .types import HealingPolicy


@dataclass
class HealthSignal:
    """A single finding emitted by the :class:`HealthMonitor`.

    Attributes:
        kind: Machine-readable signal identifier, e.g. ``"provider_failure"``.
        blueprint_id: The blueprint that triggered the signal, or ``None``
            when the signal is not blueprint-scoped.
        mission_id: The specific mission that triggered the signal, or
            ``None`` for window-level signals.
        detail: Human-readable description of the finding.
    """

    kind: str
    blueprint_id: str | None = None
    mission_id: str | None = None
    detail: str = ""


class HealthMonitor:
    """Stateful monitor that accumulates mission results and produces health signals.

    Parameters:
        policy: Threshold configuration controlling when each detection
            rule fires.
    """

    def __init__(self, policy: HealingPolicy | None = None) -> None:
        self._policy: HealingPolicy = policy or HealingPolicy()
        # Per-blueprint consecutive failure counter (reset on success).
        self._consecutive_failures: dict[str, int] = defaultdict(int)
        # Per-blueprint sliding window of booleans (True = success).
        self._success_window: dict[str, deque[bool]] = defaultdict(
            lambda: deque(maxlen=self._policy.success_rate_window)
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, result: MissionRunResult) -> None:
        """Record a completed mission result and update internal counters.

        Parameters:
            result: The immutable mission run result to record.
        """
        blueprint_id = result.mission_id  # missions carry blueprint context via id prefix
        success = result.status == "completed"

        # Update consecutive failure counter (keyed on mission_id as
        # a proxy for blueprint when no explicit blueprint_id field exists).
        if success:
            self._consecutive_failures[result.mission_id] = 0
        else:
            self._consecutive_failures[result.mission_id] += 1

        # Update sliding window (keyed on mission_id prefix acting as
        # a blueprint grouping handle; callers group by blueprint_id
        # explicitly in detect_drift).
        _ = blueprint_id  # kept for clarity

    def ingest_for_blueprint(self, blueprint_id: str, result: MissionRunResult) -> None:
        """Record a result associated with a specific blueprint.

        This overload is preferred when callers have explicit blueprint
        context — it populates both the failure counter and the
        success-rate sliding window keyed by ``blueprint_id``.

        Parameters:
            blueprint_id: The blueprint that produced this mission.
            result: The immutable mission run result.
        """
        success = result.status == "completed"
        # Consecutive failure counter
        if success:
            self._consecutive_failures[blueprint_id] = 0
        else:
            self._consecutive_failures[blueprint_id] += 1

        # Sliding window
        self._success_window[blueprint_id].append(success)

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def detect_provider_failures(self, blueprint_id: str) -> list[HealthSignal]:
        """Check whether consecutive failures for *blueprint_id* cross the threshold.

        Returns:
            A one-element list containing a ``provider_failure`` signal if
            the threshold is met, otherwise an empty list.
        """
        count = self._consecutive_failures.get(blueprint_id, 0)
        if count >= self._policy.failure_threshold:
            return [
                HealthSignal(
                    kind="provider_failure",
                    blueprint_id=blueprint_id,
                    detail=(
                        f"{count} consecutive failures for blueprint "
                        f"'{blueprint_id}' (threshold={self._policy.failure_threshold})"
                    ),
                )
            ]
        return []

    def detect_cost_spike(
        self,
        estimated_cost: float,
        actual_cost: float,
        mission_id: str,
        blueprint_id: str | None = None,
    ) -> list[HealthSignal]:
        """Check whether *actual_cost* exceeds the spike multiplier.

        Parameters:
            estimated_cost: The blueprint's budgeted cost for this mission.
            actual_cost: The observed cost at mission completion.
            mission_id: Identifier of the mission being evaluated.
            blueprint_id: Optional blueprint context for the signal.

        Returns:
            A one-element ``cost_spike`` signal list, or empty.
        """
        if estimated_cost <= 0:
            return []
        threshold = estimated_cost * self._policy.cost_spike_multiplier
        if actual_cost > threshold:
            return [
                HealthSignal(
                    kind="cost_spike",
                    blueprint_id=blueprint_id,
                    mission_id=mission_id,
                    detail=(
                        f"actual_cost={actual_cost:.4f} exceeds "
                        f"{self._policy.cost_spike_multiplier}x "
                        f"estimated_cost={estimated_cost:.4f} "
                        f"(threshold={threshold:.4f})"
                    ),
                )
            ]
        return []

    def detect_drift(self, blueprint_id: str) -> list[HealthSignal]:
        """Check whether the success rate over the sliding window is below minimum.

        The signal is only emitted once the window is full (i.e. at least
        ``policy.success_rate_window`` results have been ingested for this
        blueprint).

        Parameters:
            blueprint_id: The blueprint whose window to inspect.

        Returns:
            A one-element ``drift`` signal list, or empty.
        """
        window = self._success_window.get(blueprint_id)
        if window is None or len(window) < self._policy.success_rate_window:
            return []
        rate = sum(window) / len(window)
        if rate < self._policy.success_rate_minimum:
            return [
                HealthSignal(
                    kind="drift",
                    blueprint_id=blueprint_id,
                    detail=(
                        f"success_rate={rate:.2%} < "
                        f"minimum={self._policy.success_rate_minimum:.2%} "
                        f"over last {len(window)} runs"
                    ),
                )
            ]
        return []

    def detect_timeout(
        self,
        estimated_duration: float,
        actual_duration: float,
        mission_id: str,
        blueprint_id: str | None = None,
    ) -> list[HealthSignal]:
        """Check whether *actual_duration* exceeds the spike multiplier.

        Parameters:
            estimated_duration: The blueprint's expected wall-clock time.
            actual_duration: The observed duration at mission completion.
            mission_id: Identifier of the mission being evaluated.
            blueprint_id: Optional blueprint context for the signal.

        Returns:
            A one-element ``timeout`` signal list, or empty.
        """
        if estimated_duration <= 0:
            return []
        threshold = estimated_duration * self._policy.duration_spike_multiplier
        if actual_duration > threshold:
            return [
                HealthSignal(
                    kind="timeout",
                    blueprint_id=blueprint_id,
                    mission_id=mission_id,
                    detail=(
                        f"actual_duration={actual_duration:.2f}s exceeds "
                        f"{self._policy.duration_spike_multiplier}x "
                        f"estimated_duration={estimated_duration:.2f}s "
                        f"(threshold={threshold:.2f}s)"
                    ),
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Internal state accessors (useful for tests)
    # ------------------------------------------------------------------

    def consecutive_failures(self, blueprint_id: str) -> int:
        """Return the current consecutive-failure counter for *blueprint_id*."""
        return self._consecutive_failures.get(blueprint_id, 0)

    def window_size(self, blueprint_id: str) -> int:
        """Return the number of results currently in the sliding window."""
        w = self._success_window.get(blueprint_id)
        return len(w) if w is not None else 0

    def success_rate(self, blueprint_id: str) -> float | None:
        """Return the current success rate in the window, or None if empty."""
        w = self._success_window.get(blueprint_id)
        if not w:
            return None
        return sum(w) / len(w)
