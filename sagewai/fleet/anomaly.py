# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Anomaly detection for fleet workers.

Monitors worker behavior patterns and flags suspicious activity such as
excessive claim rates, repeated failures, missed heartbeats, or claims
for models the worker did not declare. When multiple anomaly types are
detected simultaneously, the detector recommends auto-revocation.

Usage::

    from sagewai.fleet.anomaly import (
        AnomalyThresholds,
        FleetAnomalyDetector,
    )

    detector = FleetAnomalyDetector()
    detector.record_claim("w-1", model="gpt-4o", declared_models=["gpt-4o"])
    detector.record_heartbeat("w-1")
    anomalies = detector.check_anomalies("w-1")
    if detector.should_auto_revoke("w-1"):
        # Trigger admin notification or automatic revocation
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class AnomalyThresholds:
    """Configurable thresholds for anomaly detection.

    Attributes:
        max_claims_per_minute: Maximum claims a worker can make per minute
            before triggering a rate-limit anomaly.
        max_failures_per_hour: Maximum failed reports per hour before
            triggering an excessive-failures anomaly.
        heartbeat_timeout: Duration after which a missed heartbeat
            is flagged as an anomaly.
        max_model_mismatches: Number of claims for undeclared models
            before flagging a model-mismatch anomaly.
    """

    max_claims_per_minute: int = 60
    max_failures_per_hour: int = 10
    heartbeat_timeout: timedelta = timedelta(minutes=5)
    max_model_mismatches: int = 3


@dataclass
class WorkerBehavior:
    """Tracks behavioral patterns for a single worker.

    Internal bookkeeping — not part of the public API.
    """

    worker_id: str
    claim_timestamps: list[datetime] = field(default_factory=list)
    failure_timestamps: list[datetime] = field(default_factory=list)
    model_mismatches: int = 0
    last_heartbeat: datetime | None = None


class FleetAnomalyDetector:
    """Detects anomalous worker behavior and recommends actions.

    Tracks per-worker events (claims, failures, heartbeats, model
    mismatches) and checks them against :class:`AnomalyThresholds`.

    Thread-safety note: this class is **not** thread-safe. In a
    multi-worker gateway, use one detector per asyncio event loop
    or protect with a lock.

    Args:
        thresholds: Anomaly detection thresholds. Defaults to
            :class:`AnomalyThresholds` with sensible defaults.
    """

    def __init__(
        self,
        thresholds: AnomalyThresholds | None = None,
    ) -> None:
        self._thresholds = thresholds or AnomalyThresholds()
        self._behaviors: dict[str, WorkerBehavior] = {}

    @property
    def thresholds(self) -> AnomalyThresholds:
        """Return the current anomaly thresholds."""
        return self._thresholds

    def _get_behavior(self, worker_id: str) -> WorkerBehavior:
        """Get or create behavior tracking for a worker."""
        if worker_id not in self._behaviors:
            self._behaviors[worker_id] = WorkerBehavior(worker_id=worker_id)
        return self._behaviors[worker_id]

    def record_claim(
        self,
        worker_id: str,
        model: str,
        declared_models: list[str],
    ) -> None:
        """Record a task claim event.

        Tracks the claim timestamp and checks whether the claimed model
        was in the worker's declared model list.

        Args:
            worker_id: The worker making the claim.
            model: The model the task requires.
            declared_models: Models the worker declared at registration.
        """
        behavior = self._get_behavior(worker_id)
        behavior.claim_timestamps.append(datetime.now(timezone.utc))
        if model not in declared_models:
            behavior.model_mismatches += 1
            logger.warning(
                "Worker %s claimed model '%s' not in declared models %s",
                worker_id,
                model,
                declared_models,
            )

    def record_failure(self, worker_id: str) -> None:
        """Record a failed task report from a worker."""
        behavior = self._get_behavior(worker_id)
        behavior.failure_timestamps.append(datetime.now(timezone.utc))

    def record_heartbeat(self, worker_id: str) -> None:
        """Record a heartbeat from a worker."""
        behavior = self._get_behavior(worker_id)
        behavior.last_heartbeat = datetime.now(timezone.utc)

    def check_anomalies(self, worker_id: str) -> list[str]:
        """Check a worker for anomalous behavior.

        Returns a list of anomaly type strings. Possible values:

        - ``"rate_limit"``: Claims per minute exceed threshold.
        - ``"excessive_failures"``: Failures per hour exceed threshold.
        - ``"heartbeat_timeout"``: No heartbeat within the timeout window.
        - ``"model_mismatch"``: Too many claims for undeclared models.

        Returns:
            List of anomaly type strings (empty if no anomalies).
        """
        behavior = self._behaviors.get(worker_id)
        if behavior is None:
            return []

        now = datetime.now(timezone.utc)
        anomalies: list[str] = []

        # Rate limit check: claims in the last minute
        one_minute_ago = now - timedelta(minutes=1)
        recent_claims = sum(
            1 for ts in behavior.claim_timestamps if ts >= one_minute_ago
        )
        if recent_claims > self._thresholds.max_claims_per_minute:
            anomalies.append("rate_limit")

        # Excessive failures: failures in the last hour
        one_hour_ago = now - timedelta(hours=1)
        recent_failures = sum(
            1 for ts in behavior.failure_timestamps if ts >= one_hour_ago
        )
        if recent_failures > self._thresholds.max_failures_per_hour:
            anomalies.append("excessive_failures")

        # Heartbeat timeout
        if behavior.last_heartbeat is not None:
            elapsed = now - behavior.last_heartbeat
            if elapsed > self._thresholds.heartbeat_timeout:
                anomalies.append("heartbeat_timeout")

        # Model mismatch
        if behavior.model_mismatches > self._thresholds.max_model_mismatches:
            anomalies.append("model_mismatch")

        return anomalies

    def should_auto_revoke(self, worker_id: str) -> bool:
        """Whether anomalies are severe enough to warrant auto-revocation.

        Returns ``True`` if two or more **different** anomaly types are
        detected simultaneously. A single anomaly type (e.g. a brief
        rate spike) is not sufficient for automatic revocation.
        """
        anomalies = self.check_anomalies(worker_id)
        return len(anomalies) >= 2

    def reset(self, worker_id: str) -> None:
        """Clear all tracking for a worker.

        Use after manual admin review to give the worker a clean slate.
        """
        self._behaviors.pop(worker_id, None)

    def get_tracked_workers(self) -> list[str]:
        """Return IDs of all workers currently being tracked."""
        return list(self._behaviors.keys())
