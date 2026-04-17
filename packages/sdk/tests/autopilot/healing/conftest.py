"""Shared pytest fixtures for autopilot healing tests."""

from __future__ import annotations

import pytest

from sagewai.autopilot.controller.types import MissionRunResult, StepResult
from sagewai.autopilot.healing import HealingPolicy, HealthMonitor


@pytest.fixture()
def policy() -> HealingPolicy:
    """Default HealingPolicy with canonical thresholds."""
    return HealingPolicy()


@pytest.fixture()
def strict_policy() -> HealingPolicy:
    """Tight policy for easier threshold triggering in tests."""
    return HealingPolicy(
        failure_threshold=2,
        cost_spike_multiplier=1.5,
        success_rate_window=4,
        success_rate_minimum=0.75,
        duration_spike_multiplier=2.0,
    )


@pytest.fixture()
def monitor(policy: HealingPolicy) -> HealthMonitor:
    return HealthMonitor(policy=policy)


@pytest.fixture()
def strict_monitor(strict_policy: HealingPolicy) -> HealthMonitor:
    return HealthMonitor(policy=strict_policy)


def make_result(
    mission_id: str = "m-001",
    status: str = "completed",
    duration_seconds: float = 1.0,
    error: str | None = None,
) -> MissionRunResult:
    """Helper: build a minimal MissionRunResult."""
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        steps=(StepResult(node_id="node-1", status=status),),
        duration_seconds=duration_seconds,
        error=error,
    )


def make_failed(mission_id: str = "m-001", duration_seconds: float = 1.0) -> MissionRunResult:
    return make_result(mission_id=mission_id, status="failed", duration_seconds=duration_seconds)


def make_ok(mission_id: str = "m-001", duration_seconds: float = 1.0) -> MissionRunResult:
    return make_result(mission_id=mission_id, status="completed", duration_seconds=duration_seconds)
