# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Harnessing Agent — supervisory agent that controls all LLM operations.

The HarnessingAgent wraps one or more child agents and intercepts their
LLM calls to enforce routing policies, budget limits, and model selection.
It also registers harness-specific directives for dynamic control.

Usage::

    from sagewai.engines.universal import UniversalAgent
    from sagewai.harness.agent import HarnessingAgent

    # Create child agents
    planner = UniversalAgent(name="planner", model="claude-opus-4-6")
    coder = UniversalAgent(name="coder", model="claude-sonnet-4-5-20250929")

    # Wrap with harnessing agent
    harness = HarnessingAgent(
        name="harness",
        agents=[planner, coder],
        tier_config=ModelTierConfig(
            simple="claude-haiku-4-5-20251001",
            medium="claude-sonnet-4-5-20250929",
            complex="claude-opus-4-6",
        ),
    )

    # All child agent LLM calls are now routed through the harness
    result = await planner.chat("Fix this typo")  # → routed to haiku
    result = await planner.chat("Design the auth architecture")  # → stays on opus
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.middleware import HarnessMiddleware, harness_wrap
from sagewai.harness.models import (
    ComplexityTier,
    HarnessConfig,
    ModelTierConfig,
    SpendRecord,
)

logger = logging.getLogger(__name__)


class HarnessingAgent:
    """Supervisory agent that controls LLM routing for child agents.

    The harnessing agent:
    1. Wraps child agents with the harness middleware
    2. Tracks all routing decisions and spend across agents
    3. Enforces global policies and budgets
    4. Provides audit trail of all LLM operations
    5. Supports dynamic directive-based routing

    This is the SDK-level alternative to the external proxy. While the
    proxy intercepts HTTP requests from tools like Claude Code, the
    HarnessingAgent intercepts ``_call_llm()`` inside the SDK itself.
    """

    def __init__(
        self,
        *,
        name: str = "harness",
        agents: list[Any] | None = None,
        classifier: RequestClassifier | None = None,
        tier_config: ModelTierConfig | None = None,
        config: HarnessConfig | None = None,
    ) -> None:
        self.name = name
        self._classifier = classifier or RequestClassifier()
        self._tier_config = tier_config or ModelTierConfig()
        self._config = config or HarnessConfig()
        self._agents: dict[str, Any] = {}
        self._routing_log: list[dict[str, Any]] = []
        self._spend_log: list[SpendRecord] = []
        self._created_at = time.time()

        if agents:
            for agent in agents:
                self.add_agent(agent)

    def add_agent(self, agent: Any) -> None:
        """Add a child agent and wrap it with harness middleware.

        Args:
            agent: Any BaseAgent instance.
        """
        agent_name = getattr(getattr(agent, "config", None), "name", str(id(agent)))
        harness_wrap(
            agent,
            classifier=self._classifier,
            tier_config=self._tier_config,
        )
        self._agents[agent_name] = agent
        logger.info("Harnessing agent '%s' added child '%s'", self.name, agent_name)

    def remove_agent(self, agent_name: str) -> bool:
        """Remove a child agent and disable its harness."""
        agent = self._agents.pop(agent_name, None)
        if agent is None:
            return False
        agent._harness_enabled = False
        logger.info("Harnessing agent '%s' removed child '%s'", self.name, agent_name)
        return True

    @property
    def agents(self) -> dict[str, Any]:
        """Get all managed agents."""
        return dict(self._agents)

    def get_routing_log(self) -> list[dict[str, Any]]:
        """Get the combined routing log across all child agents."""
        combined = []
        for name, agent in self._agents.items():
            for entry in getattr(agent, "_harness_log", []):
                combined.append({**entry, "agent": name})
        combined.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return combined

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics across all managed agents."""
        total_calls = 0
        total_overrides = 0
        tier_counts: dict[str, int] = {"simple": 0, "medium": 0, "complex": 0}
        model_counts: dict[str, int] = {}
        savings_calls = 0

        for agent in self._agents.values():
            for entry in getattr(agent, "_harness_log", []):
                total_calls += 1
                if entry.get("overridden"):
                    total_overrides += 1
                tier = entry.get("tier", "")
                if tier in tier_counts:
                    tier_counts[tier] += 1
                target = entry.get("target_model", "unknown")
                model_counts[target] = model_counts.get(target, 0) + 1
                # Count calls that were downgraded (saved money)
                if entry.get("overridden") and tier in ("simple", "medium"):
                    savings_calls += 1

        return {
            "name": self.name,
            "agent_count": len(self._agents),
            "total_calls": total_calls,
            "total_overrides": total_overrides,
            "override_rate": (
                total_overrides / total_calls if total_calls > 0 else 0.0
            ),
            "savings_calls": savings_calls,
            "tier_distribution": tier_counts,
            "model_distribution": model_counts,
            "uptime_seconds": time.time() - self._created_at,
        }

    def update_tier_config(self, tier_config: ModelTierConfig) -> None:
        """Update the tier configuration for all managed agents."""
        self._tier_config = tier_config
        for agent in self._agents.values():
            agent._harness_tier_config = tier_config
        logger.info("Tier config updated across %d agents", len(self._agents))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the harness across all managed agents."""
        self._config.enabled = enabled
        for agent in self._agents.values():
            agent._harness_enabled = enabled
        logger.info(
            "Harness %s across %d agents",
            "enabled" if enabled else "disabled",
            len(self._agents),
        )


def register_harness_directives(directive_engine: Any) -> None:
    """Register harness-specific directives with a DirectiveEngine.

    Registers:
    - ``@route:simple`` / ``@route:medium`` / ``@route:complex`` — force a tier
    - ``@cost:estimate`` — inline cost estimate for the request

    These directives work alongside the existing ``#model:name`` meta-directive
    which already handles explicit model selection.

    Args:
        directive_engine: A DirectiveEngine instance.
    """

    async def _route_handler(arg: str) -> str:
        """Handle @route:tier directive.

        Translates tier name to a ``#model:name`` meta-directive so the
        existing override mechanism handles the rest.
        """
        tier_name = arg.strip().lower()
        tier_map = {
            "simple": "claude-haiku-4-5-20251001",
            "medium": "claude-sonnet-4-5-20250929",
            "complex": "claude-opus-4-6",
            "cheap": "claude-haiku-4-5-20251001",
            "fast": "claude-haiku-4-5-20251001",
            "smart": "claude-opus-4-6",
            "balanced": "claude-sonnet-4-5-20250929",
        }
        model = tier_map.get(tier_name)
        if model:
            return f"[Harness: routing to {tier_name} tier → {model}]"
        return f"[Harness: unknown tier '{tier_name}', using default]"

    async def _cost_handler(arg: str) -> str:
        """Handle @cost:estimate directive.

        Returns a rough cost estimate for the current prompt size.
        """
        from sagewai.observability.costs import MODEL_PRICING

        model = arg.strip() if arg.strip() else "claude-sonnet-4-5-20250929"
        pricing = MODEL_PRICING.get(model, (3.0, 15.0))
        return (
            f"[Cost estimate for {model}: "
            f"${pricing[0]:.2f}/1M input, ${pricing[1]:.2f}/1M output]"
        )

    directive_engine.register(
        "route", "@route", _route_handler,
        description="Force a complexity tier: @route:simple, @route:medium, @route:complex",
    )
    directive_engine.register(
        "cost", "@cost", _cost_handler,
        description="Estimate cost for a model: @cost:estimate or @cost:claude-opus-4-6",
    )
    logger.info("Harness directives registered: @route, @cost")
