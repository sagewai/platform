# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.autopilot.healing.monitor.HealthMonitor."""

from __future__ import annotations

import pytest

from sagewai.autopilot.healing import HealthMonitor
from sagewai.autopilot.healing.monitor import HealthSignal

from .conftest import make_failed, make_ok

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestHealthMonitorConstruction:
    def test_default_policy(self) -> None:
        m = HealthMonitor()
        assert m._policy.failure_threshold == 3

    def test_custom_policy(self, strict_monitor: HealthMonitor) -> None:
        assert strict_monitor._policy.failure_threshold == 2

    def test_initial_state_clean(self) -> None:
        m = HealthMonitor()
        assert m.consecutive_failures("bp-x") == 0
        assert m.window_size("bp-x") == 0
        assert m.success_rate("bp-x") is None


# ---------------------------------------------------------------------------
# ingest_for_blueprint: consecutive failure tracking
# ---------------------------------------------------------------------------


class TestConsecutiveFailures:
    def test_single_failure_increments(self) -> None:
        m = HealthMonitor()
        m.ingest_for_blueprint("bp-1", make_failed())
        assert m.consecutive_failures("bp-1") == 1

    def test_multiple_failures_accumulate(self) -> None:
        m = HealthMonitor()
        for _ in range(5):
            m.ingest_for_blueprint("bp-1", make_failed())
        assert m.consecutive_failures("bp-1") == 5

    def test_success_resets_counter(self) -> None:
        m = HealthMonitor()
        for _ in range(3):
            m.ingest_for_blueprint("bp-1", make_failed())
        m.ingest_for_blueprint("bp-1", make_ok())
        assert m.consecutive_failures("bp-1") == 0

    def test_isolated_per_blueprint(self) -> None:
        m = HealthMonitor()
        for _ in range(4):
            m.ingest_for_blueprint("bp-1", make_failed())
        m.ingest_for_blueprint("bp-2", make_failed())
        assert m.consecutive_failures("bp-1") == 4
        assert m.consecutive_failures("bp-2") == 1

    def test_success_after_reset_increments_again(self) -> None:
        m = HealthMonitor()
        m.ingest_for_blueprint("bp-1", make_failed())
        m.ingest_for_blueprint("bp-1", make_ok())
        m.ingest_for_blueprint("bp-1", make_failed())
        assert m.consecutive_failures("bp-1") == 1


# ---------------------------------------------------------------------------
# detect_provider_failures
# ---------------------------------------------------------------------------


class TestDetectProviderFailures:
    def test_no_signal_below_threshold(self, strict_monitor: HealthMonitor) -> None:
        # strict_policy threshold = 2
        strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        signals = strict_monitor.detect_provider_failures("bp-1")
        assert signals == []

    def test_signal_at_threshold(self, strict_monitor: HealthMonitor) -> None:
        for _ in range(2):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        signals = strict_monitor.detect_provider_failures("bp-1")
        assert len(signals) == 1
        assert signals[0].kind == "provider_failure"
        assert signals[0].blueprint_id == "bp-1"

    def test_signal_above_threshold(self, strict_monitor: HealthMonitor) -> None:
        for _ in range(5):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        signals = strict_monitor.detect_provider_failures("bp-1")
        assert len(signals) == 1

    def test_no_signal_after_reset(self, strict_monitor: HealthMonitor) -> None:
        for _ in range(3):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        signals = strict_monitor.detect_provider_failures("bp-1")
        assert signals == []

    def test_no_signal_for_unknown_blueprint(self) -> None:
        m = HealthMonitor()
        assert m.detect_provider_failures("unknown-bp") == []


# ---------------------------------------------------------------------------
# detect_cost_spike
# ---------------------------------------------------------------------------


class TestDetectCostSpike:
    def test_no_spike_below_threshold(self) -> None:
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=1.0, actual_cost=1.9, mission_id="m-1"
        )
        assert signals == []

    def test_spike_at_exact_threshold(self) -> None:
        # actual must be strictly greater than threshold
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=1.0, actual_cost=2.0, mission_id="m-1"
        )
        assert signals == []  # 2.0 is NOT > 2.0

    def test_spike_just_above_threshold(self) -> None:
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=1.0, actual_cost=2.001, mission_id="m-1"
        )
        assert len(signals) == 1
        assert signals[0].kind == "cost_spike"
        assert signals[0].mission_id == "m-1"

    def test_spike_with_blueprint_id(self) -> None:
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=1.0, actual_cost=3.0, mission_id="m-1", blueprint_id="bp-1"
        )
        assert signals[0].blueprint_id == "bp-1"

    def test_zero_estimated_cost_no_spike(self) -> None:
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=0.0, actual_cost=100.0, mission_id="m-1"
        )
        assert signals == []

    def test_negative_estimated_cost_no_spike(self) -> None:
        m = HealthMonitor()
        signals = m.detect_cost_spike(
            estimated_cost=-1.0, actual_cost=100.0, mission_id="m-1"
        )
        assert signals == []

    def test_custom_multiplier(self, strict_monitor: HealthMonitor) -> None:
        # strict_policy multiplier = 1.5
        signals = strict_monitor.detect_cost_spike(
            estimated_cost=1.0, actual_cost=1.6, mission_id="m-1"
        )
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# detect_drift (sliding window)
# ---------------------------------------------------------------------------


class TestDetectDrift:
    def test_no_signal_when_window_not_full(self, strict_monitor: HealthMonitor) -> None:
        # strict_policy window = 4
        for _ in range(3):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        assert strict_monitor.detect_drift("bp-1") == []

    def test_no_signal_above_minimum(self, strict_monitor: HealthMonitor) -> None:
        # window=4, minimum=0.75 → need ≥ 3/4 successes to be OK
        for _ in range(3):
            strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        # rate = 0.75 which is NOT < 0.75
        assert strict_monitor.detect_drift("bp-1") == []

    def test_signal_below_minimum(self, strict_monitor: HealthMonitor) -> None:
        # 2 successes, 2 failures → rate = 0.5 < 0.75
        for _ in range(2):
            strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        for _ in range(2):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        signals = strict_monitor.detect_drift("bp-1")
        assert len(signals) == 1
        assert signals[0].kind == "drift"
        assert signals[0].blueprint_id == "bp-1"

    def test_window_slides(self, strict_monitor: HealthMonitor) -> None:
        # Fill with failures, then inject enough successes to push rate above minimum
        for _ in range(4):
            strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        # Now inject 4 more successes; deque maxlen=4 drops the old failures
        for _ in range(4):
            strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        # rate should now be 1.0 → no drift
        assert strict_monitor.detect_drift("bp-1") == []

    def test_unknown_blueprint_no_signal(self) -> None:
        m = HealthMonitor()
        assert m.detect_drift("unknown") == []

    def test_window_size_accessor(self, strict_monitor: HealthMonitor) -> None:
        for i in range(3):
            strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        assert strict_monitor.window_size("bp-1") == 3

    def test_success_rate_accessor(self, strict_monitor: HealthMonitor) -> None:
        strict_monitor.ingest_for_blueprint("bp-1", make_ok())
        strict_monitor.ingest_for_blueprint("bp-1", make_failed())
        assert strict_monitor.success_rate("bp-1") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# detect_timeout
# ---------------------------------------------------------------------------


class TestDetectTimeout:
    def test_no_signal_below_threshold(self) -> None:
        m = HealthMonitor()
        signals = m.detect_timeout(
            estimated_duration=10.0, actual_duration=25.0, mission_id="m-1"
        )
        assert signals == []

    def test_no_signal_at_exact_threshold(self) -> None:
        m = HealthMonitor()
        signals = m.detect_timeout(
            estimated_duration=10.0, actual_duration=30.0, mission_id="m-1"
        )
        # 30.0 is NOT > 30.0
        assert signals == []

    def test_signal_just_above_threshold(self) -> None:
        m = HealthMonitor()
        signals = m.detect_timeout(
            estimated_duration=10.0, actual_duration=30.1, mission_id="m-1"
        )
        assert len(signals) == 1
        assert signals[0].kind == "timeout"
        assert signals[0].mission_id == "m-1"

    def test_signal_with_blueprint_id(self) -> None:
        m = HealthMonitor()
        signals = m.detect_timeout(
            estimated_duration=10.0, actual_duration=60.0, mission_id="m-1", blueprint_id="bp-1"
        )
        assert signals[0].blueprint_id == "bp-1"

    def test_zero_estimated_duration_no_signal(self) -> None:
        m = HealthMonitor()
        signals = m.detect_timeout(
            estimated_duration=0.0, actual_duration=999.0, mission_id="m-1"
        )
        assert signals == []

    def test_custom_multiplier(self, strict_monitor: HealthMonitor) -> None:
        # strict_policy duration_spike_multiplier = 2.0
        signals = strict_monitor.detect_timeout(
            estimated_duration=10.0, actual_duration=21.0, mission_id="m-1"
        )
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# HealthSignal dataclass
# ---------------------------------------------------------------------------


class TestHealthSignal:
    def test_fields(self) -> None:
        s = HealthSignal(
            kind="provider_failure",
            blueprint_id="bp-1",
            mission_id="m-1",
            detail="3 consecutive failures",
        )
        assert s.kind == "provider_failure"
        assert s.blueprint_id == "bp-1"
        assert s.mission_id == "m-1"
        assert "3" in s.detail

    def test_optional_fields_default_none(self) -> None:
        s = HealthSignal(kind="drift")
        assert s.blueprint_id is None
        assert s.mission_id is None
        assert s.detail == ""
