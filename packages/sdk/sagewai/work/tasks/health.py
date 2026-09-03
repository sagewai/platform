# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Health of a scheduled Task, judged from its own durable cycle records (section 8.6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import SpendTotals
from sagewai.work.tasks.telemetry import ScheduledCycleTelemetry


class HealthPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    consecutive_failures: int = Field(default=3, ge=1)
    window: int = Field(default=5, ge=2)
    cost_spike_multiplier: float = Field(default=2.0, gt=1.0)
    duration_spike_multiplier: float = Field(default=3.0, gt=1.0)
    success_rate_minimum: float = Field(default=0.8, ge=0.0, le=1.0)
    cooldown_cycles: int = Field(default=5, ge=0)


class HealthSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["consecutive_failures", "cost_spike", "duration_spike", "low_success_rate"]
    detail: str
    cycle: int


class PauseSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["pause_schedule"] = "pause_schedule"
    reason: str


class RetryCycle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["retry_cycle"] = "retry_cycle"
    reason: str


class AlertOperator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["alert_operator"] = "alert_operator"
    reason: str
    severity: Literal["info", "warning", "critical"] = "warning"


HealthAction = PauseSchedule | RetryCycle | AlertOperator


def cycle_history(
    events: Sequence[TaskEvent], *, spend: Mapping[int, SpendTotals]
) -> tuple[ScheduledCycleTelemetry, ...]:
    """One record per completed cycle, from CYCLE_STARTED, CYCLE_COMPLETED, and the ledger."""
    starts: dict[int, TaskEvent] = {}
    history: list[ScheduledCycleTelemetry] = []
    for event in sorted(events, key=lambda item: item.sequence):
        if event.event_type is TaskEventType.CYCLE_STARTED:
            starts[int(event.payload_json["cycle"])] = event
        elif event.event_type is TaskEventType.CYCLE_COMPLETED:
            cycle = int(event.payload_json["cycle"])
            started = starts.get(cycle)
            history.append(
                ScheduledCycleTelemetry(
                    cycle=cycle,
                    status=str(event.payload_json["outcome"]),
                    completed_at=event.created_at,
                    duration_seconds=(
                        None
                        if started is None
                        else (event.created_at - started.created_at).total_seconds()
                    ),
                    usd_actual=spend.get(cycle, _ZERO).usd_actual,
                )
            )
    return tuple(history)


_ZERO = SpendTotals(
    usd_reserved=Decimal("0"), usd_actual=Decimal("0"), unknown_settlements=0, reservations=0
)


def evaluate_health(
    cycles: Sequence[ScheduledCycleTelemetry],
    *,
    policy: HealthPolicy,
    last_action_cycle: int | None,
) -> tuple[HealthSignal | None, HealthAction | None]:
    """One signal and, unless the cooldown holds, one action."""
    if not cycles:
        return None, None
    window = list(cycles[-policy.window :])
    latest = window[-1]
    failures = 0
    for cycle in reversed(window):
        if cycle.status != "failed":
            break
        failures += 1
    signal: HealthSignal | None = None
    action: HealthAction | None = None
    if failures >= policy.consecutive_failures:
        signal = HealthSignal(
            kind="consecutive_failures",
            detail=f"{failures} consecutive failed cycles",
            cycle=latest.cycle,
        )
        action = PauseSchedule(reason=signal.detail)
    elif failures:
        signal = HealthSignal(
            kind="consecutive_failures", detail="the last cycle failed", cycle=latest.cycle
        )
        action = RetryCycle(reason=signal.detail)
    elif len(window) >= policy.window:
        succeeded = sum(cycle.status == "succeeded" for cycle in window)
        rate = succeeded / len(window)
        costs = [cycle.usd_actual for cycle in window[:-1]]
        durations = [cycle.duration_seconds or 0.0 for cycle in window[:-1]]
        if rate < policy.success_rate_minimum:
            signal = HealthSignal(
                kind="low_success_rate",
                detail=f"success rate {rate:.2f} below {policy.success_rate_minimum}",
                cycle=latest.cycle,
            )
        elif costs and latest.usd_actual > median(costs) * Decimal(str(policy.cost_spike_multiplier)):
            signal = HealthSignal(
                kind="cost_spike",
                detail=f"cycle cost {latest.usd_actual} above {policy.cost_spike_multiplier} times the median",
                cycle=latest.cycle,
            )
        elif durations and (latest.duration_seconds or 0.0) > median(durations) * policy.duration_spike_multiplier:
            signal = HealthSignal(
                kind="duration_spike",
                detail=f"cycle duration {latest.duration_seconds}s above {policy.duration_spike_multiplier} times the median",
                cycle=latest.cycle,
            )
        if signal is not None:
            action = AlertOperator(reason=signal.detail)
    if signal is None:
        return None, None
    if last_action_cycle is not None and latest.cycle - last_action_cycle < policy.cooldown_cycles:
        return signal, None
    return signal, action


__all__ = [
    "AlertOperator",
    "HealthAction",
    "HealthPolicy",
    "HealthSignal",
    "PauseSchedule",
    "RetryCycle",
    "cycle_history",
    "evaluate_health",
]
