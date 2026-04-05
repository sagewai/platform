# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""LLM Harness middleware for BaseAgent.

Intercepts ``_call_llm()`` to classify request complexity and dynamically
route to the optimal model, enabling enterprise cost governance at the
SDK level without requiring an external proxy.

Two modes of use:

1. **Mixin** — apply ``HarnessMiddleware`` to an existing agent class::

       class MyAgent(HarnessMiddleware, UniversalAgent):
           pass

       agent = MyAgent(name="my-agent", model="claude-opus-4-6")
       agent.enable_harness(classifier=RequestClassifier(), tier_config=ModelTierConfig())

2. **Wrapper** — wrap any existing agent instance::

       from sagewai.harness.middleware import harness_wrap

       agent = UniversalAgent(name="my-agent", model="claude-opus-4-6")
       harness_wrap(agent, classifier=RequestClassifier(), tier_config=ModelTierConfig())
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sagewai.harness.classifier import ClassificationResult, RequestClassifier
from sagewai.harness.models import ComplexityTier, ModelTierConfig

logger = logging.getLogger(__name__)


class HarnessMiddleware:
    """Mixin that intercepts ``_call_llm()`` for smart model routing.

    When enabled, every LLM call is first classified by the
    :class:`RequestClassifier`, and the model is dynamically overridden
    to match the classified complexity tier.

    The original ``_current_model_override`` mechanism from BaseAgent
    is used — the middleware simply sets it before delegation, meaning
    ``#model:name`` directives still take precedence (user intent wins).

    Attributes:
        _harness_classifier: The request classifier instance.
        _harness_tier_config: Tier-to-model mapping.
        _harness_enabled: Whether the middleware is active.
        _harness_log: List of recent routing decisions for audit.
    """

    _harness_classifier: RequestClassifier | None = None
    _harness_tier_config: ModelTierConfig | None = None
    _harness_enabled: bool = False
    _harness_log: list[dict[str, Any]]

    def enable_harness(
        self,
        *,
        classifier: RequestClassifier | None = None,
        tier_config: ModelTierConfig | None = None,
        log_decisions: bool = True,
    ) -> None:
        """Enable the harness middleware on this agent.

        Args:
            classifier: Custom classifier. Uses defaults if None.
            tier_config: Custom tier mapping. Uses defaults if None.
            log_decisions: Whether to keep a routing log for audit.
        """
        self._harness_classifier = classifier or RequestClassifier()
        self._harness_tier_config = tier_config or ModelTierConfig()
        self._harness_enabled = True
        self._harness_log = [] if log_decisions else None  # type: ignore[assignment]
        logger.info(
            "Harness middleware enabled (tiers: %s/%s/%s)",
            self._harness_tier_config.simple,
            self._harness_tier_config.medium,
            self._harness_tier_config.complex,
        )

    def disable_harness(self) -> None:
        """Disable the harness middleware."""
        self._harness_enabled = False
        logger.info("Harness middleware disabled")

    @property
    def harness_routing_log(self) -> list[dict[str, Any]]:
        """Get the routing decision log."""
        return getattr(self, "_harness_log", []) or []

    async def _call_llm(self, messages: Any, tools: Any) -> Any:
        """Intercept _call_llm to classify and route.

        If the harness is enabled and no directive override is already set,
        classify the request and set the model override to the optimal model
        for the detected complexity tier.

        If a ``#model:name`` directive override is already set by the user,
        the harness defers to the user's explicit choice.
        """
        if not self._harness_enabled or self._harness_classifier is None:
            return await super()._call_llm(messages, tools)  # type: ignore[misc]

        # Check if a directive override is already set (user intent > harness)
        existing_override = getattr(self, "_current_model_override", None)
        if existing_override:
            logger.debug(
                "Harness skipping — directive override already set: %s",
                existing_override,
            )
            return await super()._call_llm(messages, tools)  # type: ignore[misc]

        # Classify the request
        msg_dicts = self._messages_to_dicts(messages)
        tool_dicts = self._tools_to_dicts(tools) if tools else None

        classification = self._harness_classifier.classify(
            msg_dicts, tools=tool_dicts
        )

        # Map tier to model
        target_model = self._harness_tier_config.model_for_tier(classification.tier)
        current_model = getattr(getattr(self, "config", None), "model", "unknown")

        # Only override if the target differs from the current model
        if target_model != current_model:
            self._current_model_override = target_model  # type: ignore[attr-defined]
            logger.info(
                "Harness routing: %s → %s (tier=%s, score=%d, confidence=%.2f)",
                current_model,
                target_model,
                classification.tier.value,
                classification.score,
                classification.confidence,
            )
        else:
            logger.debug(
                "Harness: tier=%s matches current model %s — no override",
                classification.tier.value,
                current_model,
            )

        # Log the decision
        if self._harness_log is not None:
            self._harness_log.append({
                "timestamp": time.time(),
                "tier": classification.tier.value,
                "score": classification.score,
                "confidence": classification.confidence,
                "original_model": current_model,
                "target_model": target_model,
                "overridden": target_model != current_model,
                "reason": classification.reason,
            })

        try:
            return await super()._call_llm(messages, tools)  # type: ignore[misc]
        finally:
            # Clean up the override after the call so it doesn't leak
            # to subsequent calls (unless a directive set it)
            if not existing_override:
                self._current_model_override = None  # type: ignore[attr-defined]

    @staticmethod
    def _messages_to_dicts(messages: Any) -> list[dict[str, Any]]:
        """Convert ChatMessage objects to dicts for the classifier."""
        result = []
        for m in messages:
            if hasattr(m, "role") and hasattr(m, "content"):
                result.append({"role": m.role, "content": m.content or ""})
            elif isinstance(m, dict):
                result.append(m)
        return result

    @staticmethod
    def _tools_to_dicts(tools: Any) -> list[dict[str, Any]]:
        """Convert ToolSpec objects to dicts for the classifier."""
        result = []
        for t in tools:
            if hasattr(t, "name"):
                result.append({"type": "function", "function": {"name": t.name}})
            elif isinstance(t, dict):
                result.append(t)
        return result


def harness_wrap(
    agent: Any,
    *,
    classifier: RequestClassifier | None = None,
    tier_config: ModelTierConfig | None = None,
) -> None:
    """Monkey-patch an existing agent instance with harness middleware.

    This is a convenience for wrapping agents you didn't create::

        agent = UniversalAgent(name="x", model="claude-opus-4-6")
        harness_wrap(agent, tier_config=ModelTierConfig(
            simple="claude-haiku-4-5-20251001",
            medium="claude-sonnet-4-5-20250929",
            complex="claude-opus-4-6",
        ))
        # Now all LLM calls through this agent are auto-routed

    Args:
        agent: Any BaseAgent instance.
        classifier: Custom classifier. Uses defaults if None.
        tier_config: Custom tier mapping. Uses defaults if None.
    """
    classifier = classifier or RequestClassifier()
    tier_config = tier_config or ModelTierConfig()

    agent._harness_classifier = classifier
    agent._harness_tier_config = tier_config
    agent._harness_enabled = True
    agent._harness_log = []

    # Save original _call_llm
    original_call_llm = agent._call_llm

    async def _harnessed_call_llm(messages: Any, tools: Any) -> Any:
        if not agent._harness_enabled:
            return await original_call_llm(messages, tools)

        existing_override = getattr(agent, "_current_model_override", None)
        if existing_override:
            return await original_call_llm(messages, tools)

        msg_dicts = HarnessMiddleware._messages_to_dicts(messages)
        tool_dicts = HarnessMiddleware._tools_to_dicts(tools) if tools else None
        classification = agent._harness_classifier.classify(msg_dicts, tools=tool_dicts)

        target_model = agent._harness_tier_config.model_for_tier(classification.tier)
        current_model = getattr(agent.config, "model", "unknown")

        if target_model != current_model:
            agent._current_model_override = target_model
            logger.info(
                "Harness (wrap) routing: %s → %s (tier=%s)",
                current_model, target_model, classification.tier.value,
            )

        agent._harness_log.append({
            "timestamp": time.time(),
            "tier": classification.tier.value,
            "score": classification.score,
            "original_model": current_model,
            "target_model": target_model,
            "overridden": target_model != current_model,
        })

        try:
            return await original_call_llm(messages, tools)
        finally:
            if not existing_override:
                agent._current_model_override = None

    agent._call_llm = _harnessed_call_llm
    logger.info("Harness wrapped agent '%s'", getattr(agent.config, "name", "?"))
