# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Harness router — the decision engine for LLM request routing.

Combines request classification, policy resolution, and budget enforcement
into a single ``RoutingDecision``.  Evaluation order:

1. Budget check (stop → raise, downgrade → cheapest model)
2. User override via ``force_model_header`` (if policy allows)
3. Policy resolution (most-specific matching rule wins)
4. Default classification (heuristic tier → model via tier config)
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.errors import SagewaiBudgetExceededError
from sagewai.harness.budget import HarnessBudgetManager
from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.models import (
    ComplexityTier,
    HarnessIdentity,
    ModelTierConfig,
    RoutingDecision,
)
from sagewai.harness.policy import PolicyEngine

logger = logging.getLogger(__name__)


class HarnessRouter:
    """Decision engine that routes LLM requests to the right model.

    The router evaluates budget, policy, and classification signals in a
    strict priority order and returns a ``RoutingDecision`` describing which
    model to use and why.

    Args:
        classifier: Heuristic request complexity classifier.
        policy_engine: Scoped policy resolver.
        budget_manager: Budget check / enforcement backend.
        tier_config: Default mapping of complexity tiers to models.
        allow_override: Whether callers may force a model via header.
    """

    def __init__(
        self,
        *,
        classifier: RequestClassifier,
        policy_engine: PolicyEngine,
        budget_manager: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        allow_override: bool = True,
    ) -> None:
        self._classifier = classifier
        self._policy_engine = policy_engine
        self._budget_manager = budget_manager
        self._tier_config = tier_config
        self._allow_override = allow_override

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route(
        self,
        *,
        identity: HarnessIdentity,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        requested_model: str = "",
        force_model_header: str | None = None,
    ) -> RoutingDecision:
        """Route an LLM request to the appropriate model.

        Evaluation priority (first match wins):

        1. **Budget stop** — budget exceeded with ``stop`` action raises
           ``SagewaiBudgetExceededError``.
        2. **Budget downgrade** — budget exceeded with ``downgrade`` action
           selects the cheapest model from ``tier_config``.
        3. **User override** — ``force_model_header`` is honoured when
           both the router and the matching policy allow overrides.
        4. **Policy resolution** — the most-specific matching policy's
           constraints are applied (forced model, tier cap, blocklist, etc.).
        5. **Default classification** — the request is classified by the
           heuristic classifier and the resulting tier is mapped to a model.

        Args:
            identity: Authenticated caller identity.
            messages: Chat messages (OpenAI-format dicts).
            tools: Optional tool/function definitions.
            requested_model: Model the client originally asked for.
            force_model_header: Explicit model override header value.

        Returns:
            A ``RoutingDecision`` describing the target model, tier,
            reasoning, and any policy/budget metadata.

        Raises:
            SagewaiBudgetExceededError: When the budget is exceeded and
                the configured action is ``stop``.
        """

        # ----- Step 0: Classify the request up-front (needed later) -----
        classification = self._classifier.classify(
            messages, tools=tools, model=requested_model,
        )
        tier = classification.tier
        confidence = classification.confidence

        original_model = requested_model or self._tier_config.model_for_tier(tier)

        # ----- Step 1 & 2: Budget enforcement -----
        budget_result = await self._budget_manager.check(identity)

        if budget_result.exceeded:
            action = budget_result.action  # "stop" | "downgrade" | "warn"

            if action == "stop":
                logger.warning(
                    "Budget exceeded (stop) for user=%s org=%s",
                    identity.user_id,
                    identity.org_id,
                )
                raise SagewaiBudgetExceededError(
                    f"Budget exceeded for user {identity.user_id} "
                    f"(org={identity.org_id}). Action: stop."
                )

            if action in ("downgrade", "throttle"):
                cheapest_model = self._tier_config.model_for_tier(
                    ComplexityTier.SIMPLE
                )
                logger.info(
                    "Budget exceeded (downgrade) for user=%s → %s",
                    identity.user_id,
                    cheapest_model,
                )
                return RoutingDecision(
                    target_model=cheapest_model,
                    tier=ComplexityTier.SIMPLE,
                    original_model=original_model,
                    reason="Budget exceeded — downgraded to cheapest tier",
                    budget_action="downgrade",
                    confidence=confidence,
                )

            # "warn" — fall through to normal routing but tag the decision
            logger.info(
                "Budget exceeded (warn) for user=%s — continuing",
                identity.user_id,
            )

        budget_action: str | None = (
            budget_result.action if budget_result.exceeded else None
        )

        # ----- Step 3: User override via force_model_header -----
        if force_model_header and self._allow_override:
            # Resolve policy first to check whether override is permitted
            policy_decision = await self._policy_engine.resolve(
                identity, tier, self._tier_config,
            )
            if policy_decision.allow_override:
                # Ensure the model is not on the blocklist
                if (
                    policy_decision.blocked_models
                    and force_model_header in policy_decision.blocked_models
                ):
                    logger.info(
                        "Override to '%s' blocked by policy '%s'",
                        force_model_header,
                        policy_decision.policy_name,
                    )
                else:
                    logger.debug(
                        "User override accepted: %s", force_model_header,
                    )
                    return RoutingDecision(
                        target_model=force_model_header,
                        tier=tier,
                        original_model=original_model,
                        reason=f"User override via header → {force_model_header}",
                        policy_applied=policy_decision.policy_name,
                        budget_action=budget_action,
                        confidence=confidence,
                    )

        # ----- Step 4: Policy resolution -----
        policy_decision = await self._policy_engine.resolve(
            identity, tier, self._tier_config,
        )

        if policy_decision.model:
            logger.debug(
                "Policy '%s' resolved model: %s",
                policy_decision.policy_name,
                policy_decision.model,
            )
            # If policy capped the tier, update our local tier
            resolved_tier = tier
            if policy_decision.max_tier is not None:
                tier_order = [
                    ComplexityTier.SIMPLE,
                    ComplexityTier.MEDIUM,
                    ComplexityTier.COMPLEX,
                ]
                if tier_order.index(tier) > tier_order.index(
                    policy_decision.max_tier
                ):
                    resolved_tier = policy_decision.max_tier

            return RoutingDecision(
                target_model=policy_decision.model,
                tier=resolved_tier,
                original_model=original_model,
                reason=(
                    f"Policy '{policy_decision.policy_name}' "
                    f"→ {policy_decision.model}"
                ),
                policy_applied=policy_decision.policy_name,
                budget_action=budget_action,
                confidence=confidence,
            )

        # ----- Step 5: Default — classify and map via tier config -----
        target_model = self._tier_config.model_for_tier(tier)

        logger.debug(
            "Default routing: tier=%s → %s (score=%d)",
            tier.value,
            target_model,
            classification.score,
        )

        return RoutingDecision(
            target_model=target_model,
            tier=tier,
            original_model=original_model,
            reason=classification.reason,
            policy_applied=policy_decision.policy_name,
            budget_action=budget_action,
            confidence=confidence,
        )
