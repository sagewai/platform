# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for AgentHealthMonitor state machine."""

from __future__ import annotations

import pytest

from sagewai.admin.health import AgentHealthMonitor, AgentHealthState, HealthConfig

# ------------------------------------------------------------------
# AgentHealthState enum
# ------------------------------------------------------------------


def test_health_state_values():
    """All six health states exist with correct string values."""
    assert AgentHealthState.HEALTHY.value == "healthy"
    assert AgentHealthState.DEGRADED.value == "degraded"
    assert AgentHealthState.SICK.value == "sick"
    assert AgentHealthState.RECOVERY.value == "recovery"
    assert AgentHealthState.TEST.value == "test"
    assert AgentHealthState.OOO.value == "ooo"


def test_health_state_count():
    assert len(AgentHealthState) == 6


# ------------------------------------------------------------------
# HealthConfig defaults
# ------------------------------------------------------------------


def test_health_config_defaults():
    cfg = HealthConfig()
    assert cfg.window_size == 100
    assert cfg.degraded_error_rate == 0.1
    assert cfg.sick_error_rate == 0.3
    assert cfg.recovery_successes == 5
    assert cfg.latency_degraded_ms == 5000.0
    assert cfg.latency_sick_ms == 15000.0


def test_health_config_custom():
    cfg = HealthConfig(window_size=50, degraded_error_rate=0.05, sick_error_rate=0.2)
    assert cfg.window_size == 50
    assert cfg.degraded_error_rate == 0.05
    assert cfg.sick_error_rate == 0.2


# ------------------------------------------------------------------
# Monitor: basic properties
# ------------------------------------------------------------------


def test_monitor_initial_state():
    monitor = AgentHealthMonitor("scout")
    assert monitor.agent_name == "scout"
    assert monitor.state == AgentHealthState.HEALTHY
    assert monitor.error_rate == 0.0
    assert monitor.latency_p95 == 0.0
    assert monitor.window_size == 0


def test_record_success_updates_window():
    monitor = AgentHealthMonitor("scout")
    monitor.record_success(100.0)
    assert monitor.window_size == 1
    assert monitor.error_rate == 0.0


def test_record_failure_updates_window():
    monitor = AgentHealthMonitor("scout")
    monitor.record_failure(200.0)
    assert monitor.window_size == 1
    assert monitor.error_rate == 1.0


# ------------------------------------------------------------------
# Error rate calculation
# ------------------------------------------------------------------


def test_error_rate_mixed():
    monitor = AgentHealthMonitor("scout")
    for _ in range(7):
        monitor.record_success(50.0)
    for _ in range(3):
        monitor.record_failure(50.0)

    assert abs(monitor.error_rate - 0.3) < 0.001


def test_error_rate_sliding_window_eviction():
    """Old entries are evicted when the window fills up."""
    cfg = HealthConfig(window_size=10)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Fill window with failures
    for _ in range(10):
        monitor.record_failure(50.0)
    assert monitor.error_rate == 1.0

    # Now push 10 successes — all failures evicted
    for _ in range(10):
        monitor.record_success(50.0)
    assert monitor.error_rate == 0.0


# ------------------------------------------------------------------
# Latency P95
# ------------------------------------------------------------------


def test_latency_p95_single():
    monitor = AgentHealthMonitor("scout")
    monitor.record_success(100.0)
    assert monitor.latency_p95 == 100.0


def test_latency_p95_multiple():
    monitor = AgentHealthMonitor("scout")
    for i in range(20):
        monitor.record_success(float(i * 100))  # 0, 100, 200, ..., 1900

    # P95 of 20 entries → idx = int(20 * 0.95) = 19 → latencies[19] = 1900
    assert monitor.latency_p95 == 1900.0


# ------------------------------------------------------------------
# Automatic transitions: HEALTHY → DEGRADED → SICK
# ------------------------------------------------------------------


def test_healthy_to_degraded_by_error_rate():
    cfg = HealthConfig(window_size=10, degraded_error_rate=0.1)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # 8 successes + 2 failures = 20% error rate > 10% threshold
    for _ in range(8):
        monitor.record_success(50.0)
    monitor.record_failure(50.0)
    monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.DEGRADED


def test_healthy_to_degraded_by_latency():
    cfg = HealthConfig(window_size=10, latency_degraded_ms=5000.0)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # All successes but with high latency → P95 > threshold
    for _ in range(10):
        monitor.record_success(6000.0)

    assert monitor.state == AgentHealthState.DEGRADED


def test_degraded_to_sick_by_error_rate():
    cfg = HealthConfig(
        window_size=10,
        degraded_error_rate=0.1,
        sick_error_rate=0.3,
    )
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Push into DEGRADED first (20% errors)
    for _ in range(8):
        monitor.record_success(50.0)
    monitor.record_failure(50.0)
    monitor.record_failure(50.0)
    assert monitor.state == AgentHealthState.DEGRADED

    # Now push more failures to exceed sick threshold (>30%)
    # Window: 8S + 2F = 10 entries. Add 3 more failures.
    # Eviction happens: window is [S,S,S,S,S,S,S,F,F,F] → 3/10 = 30%, not > 0.3
    # Need 4 failures: [S,S,S,S,S,S,F,F,F,F] → 4/10 = 40% > 30%
    monitor.record_failure(50.0)
    monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.SICK


def test_healthy_to_sick_directly():
    """High error rate can skip DEGRADED and go straight to SICK."""
    cfg = HealthConfig(window_size=10, sick_error_rate=0.3)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # 6 successes + 4 failures = 40% > 30% → SICK directly
    for _ in range(6):
        monitor.record_success(50.0)
    for _ in range(4):
        monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.SICK


# ------------------------------------------------------------------
# Recovery: SICK → RECOVERY → HEALTHY
# ------------------------------------------------------------------


def test_sick_to_recovery():
    cfg = HealthConfig(window_size=10, sick_error_rate=0.3, recovery_successes=10)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Get to SICK
    for _ in range(4):
        monitor.record_failure(50.0)
    for _ in range(6):
        monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.SICK

    # Push 1 success to drop error rate to 3/10=0.3 ≤ threshold → RECOVERY
    # (consecutive_successes resets on RECOVERY entry, and recovery_successes=10
    # is high enough that we won't immediately transition to HEALTHY)
    monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.RECOVERY


def test_recovery_to_healthy():
    cfg = HealthConfig(window_size=10, sick_error_rate=0.3, recovery_successes=3)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Fill window with failures → SICK
    for _ in range(10):
        monitor.record_failure(50.0)
    assert monitor.state == AgentHealthState.SICK

    # 7 successes evicts enough failures: 3/10=0.3 ≤ threshold → RECOVERY
    for _ in range(7):
        monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.RECOVERY

    # consecutive_successes resets on RECOVERY entry, need 3 fresh → HEALTHY
    for _ in range(3):
        monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.HEALTHY


def test_recovery_failure_goes_back_to_sick():
    cfg = HealthConfig(window_size=20, sick_error_rate=0.3, recovery_successes=10)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Get to SICK (7/20 = 35% error rate)
    for _ in range(7):
        monitor.record_failure(50.0)
    for _ in range(13):
        monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.SICK

    # Push successes to evict failures → RECOVERY
    for _ in range(7):
        monitor.record_success(50.0)
    assert monitor.state == AgentHealthState.RECOVERY

    # Single failure during recovery → back to SICK
    monitor.record_failure(50.0)
    assert monitor.state == AgentHealthState.SICK


# ------------------------------------------------------------------
# DEGRADED → HEALTHY (auto-recovery)
# ------------------------------------------------------------------


def test_degraded_to_healthy():
    cfg = HealthConfig(window_size=10, degraded_error_rate=0.1)
    monitor = AgentHealthMonitor("scout", config=cfg)

    # Get to DEGRADED (20% errors)
    for _ in range(8):
        monitor.record_success(50.0)
    monitor.record_failure(50.0)
    monitor.record_failure(50.0)
    assert monitor.state == AgentHealthState.DEGRADED

    # Push successes to bring error rate below threshold
    # Window evicts old entries. After 2 more successes:
    # [S,S,S,S,S,S,S,S,F,F] → [S,S,S,S,S,S,F,F,S,S] = 2/10 = 20% still DEGRADED
    # Need to push out the failures entirely.
    for _ in range(10):
        monitor.record_success(50.0)

    assert monitor.state == AgentHealthState.HEALTHY


# ------------------------------------------------------------------
# Manual overrides
# ------------------------------------------------------------------


def test_set_ooo():
    monitor = AgentHealthMonitor("scout")
    monitor.set_ooo()
    assert monitor.state == AgentHealthState.OOO


def test_set_test():
    monitor = AgentHealthMonitor("scout")
    monitor.set_test()
    assert monitor.state == AgentHealthState.TEST


def test_force_healthy_clears_window():
    monitor = AgentHealthMonitor("scout")
    for _ in range(5):
        monitor.record_failure(50.0)
    assert monitor.state != AgentHealthState.HEALTHY

    monitor.force_healthy()
    assert monitor.state == AgentHealthState.HEALTHY
    assert monitor.window_size == 0
    assert monitor.error_rate == 0.0


def test_ooo_blocks_auto_transition():
    """OOO state is not affected by recorded outcomes."""
    monitor = AgentHealthMonitor("scout")
    monitor.set_ooo()

    for _ in range(50):
        monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.OOO


def test_test_blocks_auto_transition():
    """TEST state is not affected by recorded outcomes."""
    monitor = AgentHealthMonitor("scout")
    monitor.set_test()

    for _ in range(50):
        monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.TEST


# ------------------------------------------------------------------
# State change callback
# ------------------------------------------------------------------


def test_on_state_change_callback():
    transitions: list[tuple[str, AgentHealthState, AgentHealthState]] = []

    def on_change(agent: str, prev: AgentHealthState, new: AgentHealthState) -> None:
        transitions.append((agent, prev, new))

    cfg = HealthConfig(window_size=10, degraded_error_rate=0.1)
    monitor = AgentHealthMonitor("scout", config=cfg, on_state_change=on_change)

    # Trigger HEALTHY → DEGRADED
    for _ in range(8):
        monitor.record_success(50.0)
    monitor.record_failure(50.0)
    monitor.record_failure(50.0)

    assert len(transitions) == 1
    assert transitions[0] == ("scout", AgentHealthState.HEALTHY, AgentHealthState.DEGRADED)


def test_on_state_change_not_called_for_same_state():
    """Callback is not invoked if state doesn't actually change."""
    call_count = 0

    def on_change(agent: str, prev: AgentHealthState, new: AgentHealthState) -> None:
        nonlocal call_count
        call_count += 1

    monitor = AgentHealthMonitor("scout", on_state_change=on_change)
    for _ in range(10):
        monitor.record_success(50.0)

    assert call_count == 0


def test_callback_exception_does_not_crash():
    """A raising callback doesn't break the monitor."""

    def bad_callback(agent: str, prev: AgentHealthState, new: AgentHealthState) -> None:
        raise RuntimeError("boom")

    cfg = HealthConfig(window_size=10, sick_error_rate=0.3)
    monitor = AgentHealthMonitor("scout", config=cfg, on_state_change=bad_callback)

    # Should not raise even though callback explodes
    for _ in range(5):
        monitor.record_failure(50.0)

    assert monitor.state == AgentHealthState.SICK


# ------------------------------------------------------------------
# to_dict() serialization
# ------------------------------------------------------------------


def test_to_dict():
    monitor = AgentHealthMonitor("scout")
    monitor.record_success(100.0)
    monitor.record_success(200.0)
    monitor.record_failure(300.0)

    snapshot = monitor.to_dict()
    assert snapshot["agent_name"] == "scout"
    assert snapshot["state"] in [s.value for s in AgentHealthState]
    assert snapshot["window_size"] == 3
    assert snapshot["recent_successes"] == 2
    assert snapshot["recent_failures"] == 1
    assert snapshot["error_rate"] == round(1 / 3, 4)
    assert isinstance(snapshot["latency_p95"], float)
    assert isinstance(snapshot["consecutive_successes"], int)


# ------------------------------------------------------------------
# Event hook integration
# ------------------------------------------------------------------


def test_create_event_hook():
    monitor = AgentHealthMonitor("scout")
    hook = monitor.create_event_hook()
    assert callable(hook)


@pytest.mark.asyncio
async def test_event_hook_records_success():
    """Hook records success from run_finished events."""
    monitor = AgentHealthMonitor("scout")
    hook = monitor.create_event_hook()

    # Simulate run_finished event
    from sagewai.core.events import AgentEvent

    await hook(AgentEvent.RUN_FINISHED, {"duration_ms": 150.0})

    assert monitor.window_size == 1
    assert monitor.error_rate == 0.0


@pytest.mark.asyncio
async def test_event_hook_records_failure():
    """Hook records failure from run_error events."""
    monitor = AgentHealthMonitor("scout")
    hook = monitor.create_event_hook()

    from sagewai.core.events import AgentEvent

    await hook(AgentEvent.RUN_ERROR, {"duration_ms": 500.0, "error": "timeout"})

    assert monitor.window_size == 1
    assert monitor.error_rate == 1.0


@pytest.mark.asyncio
async def test_event_hook_ignores_other_events():
    """Hook ignores events that are not run_finished or run_error."""
    monitor = AgentHealthMonitor("scout")
    hook = monitor.create_event_hook()

    from sagewai.core.events import AgentEvent

    await hook(AgentEvent.RUN_STARTED, {"agent": "scout"})
    await hook(AgentEvent.STEP_STARTED, {"step": 1})

    assert monitor.window_size == 0


@pytest.mark.asyncio
async def test_event_hook_drives_state_transitions():
    """Hook integration: enough errors through the hook → state transition."""
    cfg = HealthConfig(window_size=10, degraded_error_rate=0.1, sick_error_rate=0.3)
    monitor = AgentHealthMonitor("scout", config=cfg)
    hook = monitor.create_event_hook()

    from sagewai.core.events import AgentEvent

    # 8 successes + 2 errors → 20% error rate → DEGRADED
    for _ in range(8):
        await hook(AgentEvent.RUN_FINISHED, {"duration_ms": 100.0})
    for _ in range(2):
        await hook(AgentEvent.RUN_ERROR, {"duration_ms": 100.0, "error": "fail"})

    assert monitor.state == AgentHealthState.DEGRADED


# ------------------------------------------------------------------
# HealthSnapshot model
# ------------------------------------------------------------------


def test_health_snapshot_from_to_dict():
    """HealthSnapshot can be populated from monitor.to_dict()."""
    from sagewai.admin.models import HealthSnapshot

    monitor = AgentHealthMonitor("scout")
    monitor.record_success(100.0)
    monitor.record_failure(200.0)

    snapshot = HealthSnapshot(**monitor.to_dict())
    assert snapshot.agent_name == "scout"
    assert snapshot.window_size == 2
    assert snapshot.recent_successes == 1
    assert snapshot.recent_failures == 1
