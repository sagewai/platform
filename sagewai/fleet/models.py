# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet data models for enterprise worker registration and enrollment.

Defines the Pydantic v2 models used by the Fleet subsystem:

- ``WorkerCapabilities``: declared by workers at registration time.
- ``WorkerRecord``: persisted record of a registered fleet worker.
- ``EnrollmentKey``: pre-shared key for fleet-scale worker onboarding.
- ``WorkerApprovalStatus``: approval state machine for worker registration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class WorkerApprovalStatus(str, Enum):
    """Approval state for a fleet worker registration.

    Workers start as PENDING, are moved to APPROVED by an admin,
    and can be REJECTED or REVOKED at any time.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class WorkerCapabilities(BaseModel):
    """Capabilities declared by a worker at registration time.

    Attributes:
        models_supported: Raw model names the worker can serve
            (e.g. ``["openai/gpt-4o", "ollama/llama3:70b"]``).
        models_canonical: Auto-filled by ``ModelNormalizer.canonical_list()``
            during registration. Used for matching at claim time.
        max_concurrent: Maximum number of concurrent workflow runs.
        labels: Arbitrary key-value metadata for label-based routing.
        pool: Worker pool name (like a Temporal task queue).
        sdk_version: Version of the Sagewai SDK running on the worker.
    """

    models_supported: list[str] = Field(
        default_factory=list,
        description="Raw model names the worker can serve",
    )
    models_canonical: list[str] = Field(
        default_factory=list,
        description="Normalized model names (auto-filled by ModelNormalizer)",
    )
    max_concurrent: int = Field(
        default=1,
        ge=1,
        description="Maximum concurrent workflow runs",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key-value labels for routing",
    )
    pool: str = Field(
        default="default",
        description="Worker pool name",
    )
    sdk_version: str = Field(
        default="",
        description="Sagewai SDK version on the worker",
    )


class WorkerRecord(BaseModel):
    """Persisted record of a registered fleet worker.

    Created when a worker registers via the fleet gateway and stored
    in the ``workers`` table with fleet-specific columns.

    Attributes:
        id: Unique worker identifier (UUID).
        name: Human-readable worker name.
        org_id: Organization that owns this worker.
        capabilities: Declared worker capabilities.
        approval_status: Current approval state.
        last_heartbeat: Last heartbeat timestamp (None if never seen).
        last_probe_at: Last health probe timestamp.
        probe_status: Result of the last health probe.
        registered_at: When the worker first registered.
        approved_at: When the worker was approved (None if not yet).
        approved_by: Who approved the worker (admin user ID).
    """

    id: str = Field(description="Worker UUID")
    name: str = Field(description="Human-readable worker name")
    org_id: str = Field(description="Owning organization ID")
    capabilities: WorkerCapabilities = Field(
        default_factory=WorkerCapabilities,
        description="Declared worker capabilities",
    )
    approval_status: WorkerApprovalStatus = Field(
        default=WorkerApprovalStatus.PENDING,
        description="Current approval state",
    )
    last_heartbeat: datetime | None = Field(
        default=None,
        description="Last heartbeat timestamp",
    )
    last_probe_at: datetime | None = Field(
        default=None,
        description="Last health probe timestamp",
    )
    probe_status: str | None = Field(
        default=None,
        description='Health probe result: "healthy", "degraded", or "unknown"',
    )
    registered_at: datetime = Field(description="Registration timestamp")
    approved_at: datetime | None = Field(
        default=None,
        description="Approval timestamp",
    )
    approved_by: str | None = Field(
        default=None,
        description="Admin user ID who approved",
    )


class EnrollmentKey(BaseModel):
    """Pre-shared key for fleet-scale worker registration.

    Enrollment keys allow bulk worker onboarding without individual
    admin approval. Each key can be scoped to specific pools and
    models, with optional usage limits and expiration.

    Attributes:
        id: Unique key identifier (UUID).
        org_id: Organization that created this key.
        name: Human-readable label for the key.
        key_hash: bcrypt hash of the actual enrollment key value.
        max_uses: Maximum number of registrations (None = unlimited).
        current_uses: Number of times this key has been used.
        expires_at: Expiration timestamp (None = never expires).
        allowed_pools: Pools workers can join with this key (empty = any).
        allowed_models: Models workers can declare with this key (empty = any).
        created_at: When the key was created.
        created_by: Admin user ID who created the key.
        revoked: Whether the key has been revoked.
    """

    id: str = Field(description="Enrollment key UUID")
    org_id: str = Field(description="Owning organization ID")
    name: str = Field(description="Human-readable label")
    key_hash: str = Field(description="bcrypt hash of the enrollment key")
    max_uses: int | None = Field(
        default=None,
        description="Max registrations (None = unlimited)",
    )
    current_uses: int = Field(
        default=0,
        ge=0,
        description="Number of times used",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Expiration timestamp (None = never)",
    )
    allowed_pools: list[str] = Field(
        default_factory=list,
        description="Allowed pools (empty = any)",
    )
    allowed_models: list[str] = Field(
        default_factory=list,
        description="Allowed models (empty = any)",
    )
    created_at: datetime = Field(description="Creation timestamp")
    created_by: str = Field(description="Admin user ID")
    revoked: bool = Field(default=False, description="Whether revoked")

    def is_expired(self) -> bool:
        """Check if this enrollment key has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def is_exhausted(self) -> bool:
        """Check if this enrollment key has reached its usage limit."""
        if self.max_uses is None:
            return False
        return self.current_uses >= self.max_uses

    def is_usable(self) -> bool:
        """Check if this enrollment key can still be used for registration."""
        return not self.revoked and not self.is_expired() and not self.is_exhausted()
