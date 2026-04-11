# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Enterprise Fleet — remote worker registration, enrollment, and model-aware routing.

Enables workers to register over HTTPS, authenticate with JWT tokens,
and claim tasks through long-poll. Workers declare model capabilities
at registration; the scheduler matches runs to workers that support
the required model.

This is a premium enterprise feature gated by ``SAGEWAI_LICENSE_KEY``.

Usage::

    from sagewai.fleet import (
        EnrollmentKey,
        FleetAnomalyDetector,
        FleetDispatcher,
        FleetPayloadEncryption,
        FleetRegistry,
        InMemoryFleetRegistry,
        LLMHealthProbe,
        LLMProbeResult,
        MTLSConfig,
        MTLSVerifier,
        ModelNormalizer,
        WorkerApprovalStatus,
        WorkerCapabilities,
        WorkerRecord,
        WRTTokenManager,
    )
"""

from sagewai.fleet.anomaly import (
    AnomalyThresholds,
    FleetAnomalyDetector,
)
from sagewai.fleet.audit import (
    FleetAuditBackend,
    FleetAuditEvent,
    FleetAuditEventType,
    InMemoryFleetAuditBackend,
    PostgresFleetAuditBackend,
)
from sagewai.fleet.auth import (
    InMemoryRevocationStore,
    WRTRevocationStore,
    WRTTokenManager,
)
from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore, TaskStore
from sagewai.fleet.encryption import FleetPayloadEncryption
from sagewai.fleet.models import (
    EnrollmentKey,
    WorkerApprovalStatus,
    WorkerCapabilities,
    WorkerRecord,
)
from sagewai.fleet.mtls import MTLSConfig, MTLSVerifier
from sagewai.fleet.normalizer import ModelNormalizer
from sagewai.fleet.probe import LLMHealthProbe, LLMProbeResult
from sagewai.fleet.registry import (
    FleetRegistry,
    InMemoryFleetRegistry,
    PostgresFleetRegistry,
)

__all__ = [
    "AnomalyThresholds",
    "EnrollmentKey",
    "FleetAnomalyDetector",
    "FleetAuditBackend",
    "FleetAuditEvent",
    "FleetAuditEventType",
    "FleetDispatcher",
    "FleetPayloadEncryption",
    "FleetRegistry",
    "InMemoryFleetAuditBackend",
    "InMemoryFleetRegistry",
    "InMemoryRevocationStore",
    "InMemoryTaskStore",
    "LLMHealthProbe",
    "LLMProbeResult",
    "MTLSConfig",
    "MTLSVerifier",
    "ModelNormalizer",
    "PostgresFleetAuditBackend",
    "PostgresFleetRegistry",
    "TaskStore",
    "WorkerApprovalStatus",
    "WorkerCapabilities",
    "WorkerRecord",
    "WRTRevocationStore",
    "WRTTokenManager",
]
