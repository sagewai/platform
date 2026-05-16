# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.fleet.anomaly — anomaly detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sagewai.fleet.anomaly import (
    AnomalyThresholds,
    FleetAnomalyDetector,
    WorkerBehavior,
)


# ------------------------------------------------------------------
# AnomalyThresholds defaults
# ------------------------------------------------------------------


class TestAnomalyThresholds:
    def test_defaults(self):
        t = AnomalyThresholds()
        assert t.max_claims_per_minute == 60
        assert t.max_failures_per_hour == 10
        assert t.heartbeat_timeout == timedelta(minutes=5)
        assert t.max_model_mismatches == 3

    def test_custom(self):
        t = AnomalyThresholds(max_claims_per_minute=10, max_model_mismatches=1)
        assert t.max_claims_per_minute == 10
        assert t.max_model_mismatches == 1


# ------------------------------------------------------------------
# WorkerBehavior
# ------------------------------------------------------------------


class TestWorkerBehavior:
    def test_defaults(self):
        b = WorkerBehavior(worker_id="w-1")
        assert b.worker_id == "w-1"
        assert b.claim_timestamps == []
        assert b.failure_timestamps == []
        assert b.model_mismatches == 0
        assert b.last_heartbeat is None


# ------------------------------------------------------------------
# FleetAnomalyDetector — recording
# ------------------------------------------------------------------


class TestAnomalyDetectorRecording:
    def test_record_claim_normal(self):
        d = FleetAnomalyDetector()
        d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        assert d.check_anomalies("w-1") == []

    def test_record_claim_model_mismatch(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(max_model_mismatches=1)
        )
        d.record_claim("w-1", model="gpt-4o", declared_models=["llama3:8b"])
        d.record_claim("w-1", model="claude-3", declared_models=["llama3:8b"])
        anomalies = d.check_anomalies("w-1")
        assert "model_mismatch" in anomalies

    def test_record_failure(self):
        d = FleetAnomalyDetector()
        d.record_failure("w-1")
        # One failure shouldn't trigger anything
        assert d.check_anomalies("w-1") == []

    def test_record_heartbeat(self):
        d = FleetAnomalyDetector()
        d.record_heartbeat("w-1")
        assert d.check_anomalies("w-1") == []


# ------------------------------------------------------------------
# FleetAnomalyDetector — anomaly checks
# ------------------------------------------------------------------


class TestAnomalyDetectorChecks:
    def test_no_tracking_returns_empty(self):
        d = FleetAnomalyDetector()
        assert d.check_anomalies("unknown-worker") == []

    def test_rate_limit(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(max_claims_per_minute=5)
        )
        for _ in range(6):
            d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        anomalies = d.check_anomalies("w-1")
        assert "rate_limit" in anomalies

    def test_excessive_failures(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(max_failures_per_hour=3)
        )
        for _ in range(4):
            d.record_failure("w-1")
        anomalies = d.check_anomalies("w-1")
        assert "excessive_failures" in anomalies

    def test_heartbeat_timeout(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(heartbeat_timeout=timedelta(seconds=1))
        )
        # Set heartbeat to the past
        behavior = d._get_behavior("w-1")
        behavior.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=5)
        anomalies = d.check_anomalies("w-1")
        assert "heartbeat_timeout" in anomalies

    def test_heartbeat_ok_when_recent(self):
        d = FleetAnomalyDetector()
        d.record_heartbeat("w-1")
        anomalies = d.check_anomalies("w-1")
        assert "heartbeat_timeout" not in anomalies

    def test_no_heartbeat_no_timeout(self):
        """Workers with no heartbeat recorded should not trigger timeout."""
        d = FleetAnomalyDetector()
        d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        anomalies = d.check_anomalies("w-1")
        assert "heartbeat_timeout" not in anomalies


# ------------------------------------------------------------------
# FleetAnomalyDetector — auto-revoke
# ------------------------------------------------------------------


class TestAnomalyDetectorAutoRevoke:
    def test_single_anomaly_no_revoke(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(max_claims_per_minute=2)
        )
        for _ in range(3):
            d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        assert d.should_auto_revoke("w-1") is False

    def test_two_anomalies_revoke(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(
                max_claims_per_minute=2,
                max_model_mismatches=1,
            )
        )
        for _ in range(3):
            d.record_claim("w-1", model="gpt-4o", declared_models=["llama3:8b"])
        assert d.should_auto_revoke("w-1") is True

    def test_no_anomalies_no_revoke(self):
        d = FleetAnomalyDetector()
        d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        assert d.should_auto_revoke("w-1") is False


# ------------------------------------------------------------------
# FleetAnomalyDetector — reset and tracking
# ------------------------------------------------------------------


class TestAnomalyDetectorReset:
    def test_reset_clears_tracking(self):
        d = FleetAnomalyDetector(
            AnomalyThresholds(max_claims_per_minute=2)
        )
        for _ in range(3):
            d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        assert "rate_limit" in d.check_anomalies("w-1")
        d.reset("w-1")
        assert d.check_anomalies("w-1") == []

    def test_reset_unknown_worker_noop(self):
        d = FleetAnomalyDetector()
        d.reset("nonexistent")  # Should not raise

    def test_get_tracked_workers(self):
        d = FleetAnomalyDetector()
        d.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
        d.record_claim("w-2", model="gpt-4o", declared_models=["gpt-4o"])
        tracked = d.get_tracked_workers()
        assert set(tracked) == {"w-1", "w-2"}

    def test_thresholds_property(self):
        t = AnomalyThresholds(max_claims_per_minute=99)
        d = FleetAnomalyDetector(t)
        assert d.thresholds.max_claims_per_minute == 99
