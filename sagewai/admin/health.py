# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Agent health state machine with monitoring.

Tracks error rates and latency via a sliding window, automatically
transitioning agents between health states (HEALTHY → DEGRADED → SICK)
and supporting manual overrides for maintenance.

Usage::

    from sagewai.admin.health import AgentHealthMonitor

    monitor = AgentHealthMonitor("scout")
    agent.on_event(monitor.create_event_hook())

    await agent.chat("Hello")
    print(monitor.state)       # AgentHealthState.HEALTHY
    print(monitor.error_rate)  # 0.0
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AgentHealthState(str, Enum):
    """Health states for an agent."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    SICK = "sick"
    RECOVERY = "recovery"
    TEST = "test"
    OOO = "ooo"


@dataclass
class HealthConfig:
    """Configurable thresholds for health state transitions."""

    window_size: int = 100
    degraded_error_rate: float = 0.1
    sick_error_rate: float = 0.3
    recovery_successes: int = 5
    latency_degraded_ms: float = 5000.0
    latency_sick_ms: float = 15000.0


@dataclass
class _WindowEntry:
    """A single entry in the sliding window."""

    success: bool
    latency_ms: float


class AgentHealthMonitor:
    """Monitors agent health via a sliding window of call outcomes.

    Automatically transitions between health states based on configurable
    error rate and latency thresholds. Supports manual overrides for
    maintenance and testing.

    Parameters
    ----------
    agent_name:
        Name of the agent being monitored.
    config:
        Health thresholds. Defaults to :class:`HealthConfig`.
    on_state_change:
        Optional callback invoked on state transitions.
        Signature: ``(agent_name, previous_state, new_state) -> None``.
    """

    def __init__(
        self,
        agent_name: str,
        config: HealthConfig | None = None,
        on_state_change: Callable[[str, AgentHealthState, AgentHealthState], None] | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._config = config or HealthConfig()
        self._state = AgentHealthState.HEALTHY
        self._window: deque[_WindowEntry] = deque(maxlen=self._config.window_size)
        self._consecutive_successes = 0
        self._on_state_change = on_state_change

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def state(self) -> AgentHealthState:
        return self._state

    @property
    def error_rate(self) -> float:
        """Error rate over the sliding window (0.0 to 1.0)."""
        if not self._window:
            return 0.0
        failures = sum(1 for e in self._window if not e.success)
        return failures / len(self._window)

    @property
    def latency_p95(self) -> float:
        """P95 latency in milliseconds over the sliding window."""
        if not self._window:
            return 0.0
        latencies = sorted(e.latency_ms for e in self._window)
        idx = int(len(latencies) * 0.95)
        idx = min(idx, len(latencies) - 1)
        return latencies[idx]

    @property
    def window_size(self) -> int:
        """Current number of entries in the sliding window."""
        return len(self._window)

    def record_success(self, latency_ms: float = 0.0) -> None:
        """Record a successful call."""
        self._window.append(_WindowEntry(success=True, latency_ms=latency_ms))
        self._consecutive_successes += 1
        self._check_transition()

    def record_failure(self, latency_ms: float = 0.0) -> None:
        """Record a failed call."""
        self._window.append(_WindowEntry(success=False, latency_ms=latency_ms))
        self._consecutive_successes = 0
        self._check_transition()

    # ------------------------------------------------------------------
    # Manual overrides
    # ------------------------------------------------------------------

    def set_ooo(self) -> None:
        """Manually take the agent offline (maintenance)."""
        self._transition_to(AgentHealthState.OOO)

    def set_test(self) -> None:
        """Put the agent in test mode."""
        self._transition_to(AgentHealthState.TEST)

    def force_healthy(self) -> None:
        """Force the agent back to HEALTHY and clear the window."""
        self._window.clear()
        self._consecutive_successes = 0
        self._transition_to(AgentHealthState.HEALTHY)

    # ------------------------------------------------------------------
    # Event hook for BaseAgent integration
    # ------------------------------------------------------------------

    def create_event_hook(self):
        """Create an event hook that auto-records health from BaseAgent events.

        Listens for ``RUN_FINISHED`` and ``RUN_ERROR`` events.

        Returns:
            A callable matching ``EventCallback``.
        """
        monitor = self

        async def hook(event: Any, data: dict[str, Any]) -> None:
            event_value = event.value if hasattr(event, "value") else str(event)

            if event_value == "run_finished":
                duration = data.get("duration_ms", 0.0)
                monitor.record_success(duration)
            elif event_value == "run_error":
                duration = data.get("duration_ms", 0.0)
                monitor.record_failure(duration)

        return hook

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a snapshot of current health state."""
        failures = sum(1 for e in self._window if not e.success)
        successes = len(self._window) - failures
        return {
            "agent_name": self._agent_name,
            "state": self._state.value,
            "error_rate": round(self.error_rate, 4),
            "latency_p95": round(self.latency_p95, 2),
            "window_size": len(self._window),
            "recent_successes": successes,
            "recent_failures": failures,
            "consecutive_successes": self._consecutive_successes,
        }

    # ------------------------------------------------------------------
    # Internal state machine
    # ------------------------------------------------------------------

    def _check_transition(self) -> None:
        """Evaluate and apply state transitions based on current metrics."""
        # Manual states (OOO, TEST) don't auto-transition
        if self._state in (AgentHealthState.OOO, AgentHealthState.TEST):
            return

        error_rate = self.error_rate
        latency = self.latency_p95
        cfg = self._config

        if self._state == AgentHealthState.HEALTHY:
            if error_rate > cfg.sick_error_rate or latency > cfg.latency_sick_ms:
                self._transition_to(AgentHealthState.SICK)
            elif error_rate > cfg.degraded_error_rate or latency > cfg.latency_degraded_ms:
                self._transition_to(AgentHealthState.DEGRADED)

        elif self._state == AgentHealthState.DEGRADED:
            if error_rate > cfg.sick_error_rate or latency > cfg.latency_sick_ms:
                self._transition_to(AgentHealthState.SICK)
            elif error_rate <= cfg.degraded_error_rate and latency <= cfg.latency_degraded_ms:
                self._transition_to(AgentHealthState.HEALTHY)

        elif self._state == AgentHealthState.SICK:
            if error_rate <= cfg.sick_error_rate:
                self._transition_to(AgentHealthState.RECOVERY)

        elif self._state == AgentHealthState.RECOVERY:
            # Any failure during recovery → back to SICK
            if self._window and not self._window[-1].success:
                self._transition_to(AgentHealthState.SICK)
            elif self._consecutive_successes >= cfg.recovery_successes:
                self._transition_to(AgentHealthState.HEALTHY)

    def _transition_to(self, new_state: AgentHealthState) -> None:
        """Transition to a new state, firing callbacks."""
        if new_state == self._state:
            return

        previous = self._state
        self._state = new_state

        # Reset consecutive counter on RECOVERY entry so we require
        # N *fresh* successes before declaring healthy.
        if new_state == AgentHealthState.RECOVERY:
            self._consecutive_successes = 0

        logger.info(
            "Agent '%s' health: %s → %s (error_rate=%.2f, p95=%.1fms)",
            self._agent_name,
            previous.value,
            new_state.value,
            self.error_rate,
            self.latency_p95,
        )

        if self._on_state_change:
            try:
                self._on_state_change(self._agent_name, previous, new_state)
            except Exception:  # noqa: broad-exception-caught — callback must not break FSM
                logger.exception("Error in health state change callback")
