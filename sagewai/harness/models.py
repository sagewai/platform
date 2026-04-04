# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Pydantic v2 data models for the LLM Harness."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ComplexityTier(str, Enum):
    """Request complexity tier for model routing."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ModelTierConfig(BaseModel):
    """Maps complexity tiers to target model names."""

    simple: str = "claude-haiku-4-5-20251001"
    medium: str = "claude-sonnet-4-5-20250929"
    complex: str = "claude-opus-4-6"

    def model_for_tier(self, tier: ComplexityTier) -> str:
        """Return the target model for a given tier."""
        return getattr(self, tier.value)


class PolicyScope(BaseModel):
    """Scope for a policy rule — narrows from org down to user."""

    org_id: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    user_id: str | None = None

    def specificity(self) -> int:
        """Higher = more specific scope. User > project > team > org."""
        score = 0
        if self.org_id:
            score += 1
        if self.team_id:
            score += 2
        if self.project_id:
            score += 4
        if self.user_id:
            score += 8
        return score

    def matches(self, identity: HarnessIdentity) -> bool:
        """Check if this scope matches a given identity."""
        if self.org_id and self.org_id != identity.org_id:
            return False
        if self.team_id and self.team_id != identity.team_id:
            return False
        if self.project_id and self.project_id != identity.project_id:
            return False
        if self.user_id and self.user_id != identity.user_id:
            return False
        return True


class PolicyRule(BaseModel):
    """A routing policy rule scoped to org/team/project/user."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    description: str = ""
    scope: PolicyScope = Field(default_factory=PolicyScope)
    priority: int = 0
    tier_overrides: dict[str, str] = Field(default_factory=dict)
    blocked_models: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)
    max_tier: ComplexityTier | None = None
    force_model: str | None = None
    allow_override: bool = False
    enabled: bool = True


class HarnessIdentity(BaseModel):
    """Identity extracted from a harness API key."""

    key_id: str
    user_id: str
    org_id: str = "default"
    team_id: str | None = None
    project_id: str | None = None
    name: str = ""


class HarnessKey(BaseModel):
    """A harness API key with scoping and budget."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    key_hash: str = ""
    key_suffix: str = ""
    name: str = ""
    user_id: str = ""
    org_id: str = "default"
    team_id: str | None = None
    project_id: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    max_budget_daily_usd: float | None = None
    max_budget_monthly_usd: float | None = None
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)
    expires_at: float | None = None

    def is_expired(self) -> bool:
        """Check if the key has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_identity(self) -> HarnessIdentity:
        """Convert key to an identity for policy matching."""
        return HarnessIdentity(
            key_id=self.id,
            user_id=self.user_id,
            org_id=self.org_id,
            team_id=self.team_id,
            project_id=self.project_id,
            name=self.name,
        )


class RoutingDecision(BaseModel):
    """Result of the harness routing process."""

    target_model: str
    tier: ComplexityTier
    original_model: str
    reason: str
    policy_applied: str | None = None
    budget_action: str | None = None
    confidence: float = 0.0


class SpendRecord(BaseModel):
    """A single LLM request spend record."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    user_id: str = ""
    team_id: str | None = None
    project_id: str | None = None
    org_id: str = "default"
    model_requested: str = ""
    model_used: str = ""
    complexity_tier: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    policy_applied: str | None = None
    budget_action: str | None = None
    key_id: str = ""


class HarnessAuditEvent(BaseModel):
    """Audit event for the harness."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    user_id: str = ""
    org_id: str = "default"
    details: dict[str, Any] = Field(default_factory=dict)


class HarnessConfig(BaseModel):
    """Global harness configuration."""

    enabled: bool = True
    default_tier_config: ModelTierConfig = Field(default_factory=ModelTierConfig)
    default_action_on_budget_exceeded: Literal["warn", "downgrade", "block"] = (
        "downgrade"
    )
    allow_model_override: bool = True
    log_requests: bool = True
    transparency_headers: bool = True
