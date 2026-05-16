# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Policy engine for the LLM Harness.

Resolves routing policies based on identity scope. Multiple policies
can match — the most specific scope with highest priority wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sagewai.harness.models import (
    ComplexityTier,
    HarnessIdentity,
    ModelTierConfig,
    PolicyRule,
)

logger = logging.getLogger(__name__)


@dataclass
class PolicyDecision:
    """Result of policy resolution."""

    model: str | None = None
    tier_overrides: dict[str, str] | None = None
    max_tier: ComplexityTier | None = None
    allow_override: bool = True
    blocked_models: list[str] | None = None
    policy_name: str | None = None
    policy_id: str | None = None


@runtime_checkable
class PolicyStore(Protocol):
    """Protocol for policy storage backends."""

    async def list_policies(self, *, org_id: str | None = None) -> list[PolicyRule]:
        """List all enabled policies, optionally filtered by org."""
        ...

    async def get_policy(self, policy_id: str) -> PolicyRule | None:
        """Get a single policy by ID."""
        ...

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Create a new policy."""
        ...

    async def update_policy(self, policy_id: str, rule: PolicyRule) -> PolicyRule | None:
        """Update an existing policy."""
        ...

    async def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy."""
        ...


class PolicyEngine:
    """Resolves routing decisions from matching policies.

    Resolution order:
    1. Filter to enabled policies whose scope matches the identity
    2. Sort by scope specificity (user > project > team > org)
    3. Within the same specificity, sort by priority (highest first)
    4. Apply the winning policy's constraints

    Usage::

        engine = PolicyEngine(store=my_store)
        decision = await engine.resolve(identity, tier, tier_config)
    """

    def __init__(self, store: PolicyStore) -> None:
        self._store = store

    async def resolve(
        self,
        identity: HarnessIdentity,
        tier: ComplexityTier,
        tier_config: ModelTierConfig,
    ) -> PolicyDecision:
        """Resolve the best matching policy for an identity.

        Args:
            identity: The authenticated user identity.
            tier: The classified complexity tier.
            tier_config: Default tier-to-model mapping.

        Returns:
            PolicyDecision with the resolved model and constraints.
        """
        policies = await self._store.list_policies(org_id=identity.org_id)

        # Filter to enabled policies that match this identity
        matching: list[tuple[int, int, PolicyRule]] = []
        for policy in policies:
            if not policy.enabled:
                continue
            if policy.scope.matches(identity):
                specificity = policy.scope.specificity()
                matching.append((specificity, policy.priority, policy))

        if not matching:
            return PolicyDecision(allow_override=True)

        # Sort: highest specificity first, then highest priority
        matching.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, _, winner = matching[0]

        logger.debug(
            "Policy '%s' matched for user=%s (specificity=%d, priority=%d)",
            winner.name,
            identity.user_id,
            winner.scope.specificity(),
            winner.priority,
        )

        # Build decision from winning policy
        decision = PolicyDecision(
            allow_override=winner.allow_override,
            blocked_models=winner.blocked_models or None,
            policy_name=winner.name,
            policy_id=winner.id,
        )

        # Force model overrides everything
        if winner.force_model:
            decision.model = winner.force_model
            return decision

        # Max tier caps the classified tier
        if winner.max_tier is not None:
            decision.max_tier = winner.max_tier
            tier_order = [ComplexityTier.SIMPLE, ComplexityTier.MEDIUM, ComplexityTier.COMPLEX]
            if tier_order.index(tier) > tier_order.index(winner.max_tier):
                tier = winner.max_tier

        # Tier overrides map specific tiers to different models
        if winner.tier_overrides:
            decision.tier_overrides = winner.tier_overrides
            override_model = winner.tier_overrides.get(tier.value)
            if override_model:
                decision.model = override_model
                return decision

        # Default: use the tier config
        decision.model = tier_config.model_for_tier(tier)
        return decision
