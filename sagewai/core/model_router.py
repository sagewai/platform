# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Resource-aware model routing — dynamically select models based on rules."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    """A single routing rule: condition + target model."""

    condition: Callable[[str, dict[str, Any]], bool]
    model: str


class ModelRouter:
    """Selects the optimal model for each query based on routing rules.

    Rules are evaluated in order; the first matching rule determines the model.
    If no rules match, the *default_model* is used.

    Usage::

        router = ModelRouter(
            rules=[
                short_query_rule(threshold=30, model="gpt-4o-mini"),
                tool_heavy_rule(model="gpt-4o"),
            ],
            default_model="gpt-4o",
        )
        model = router.select_model(query, context)
    """

    def __init__(
        self,
        *,
        rules: list[RoutingRule],
        default_model: str,
    ) -> None:
        self.rules = rules
        self.default_model = default_model

    def select_model(self, query: str, context: dict[str, Any]) -> str:
        """Evaluate rules and return the selected model ID."""
        for rule in self.rules:
            try:
                if rule.condition(query, context):
                    logger.debug("Model routed to %s by rule", rule.model)
                    return rule.model
            except Exception:
                logger.warning("Rule evaluation failed, skipping", exc_info=True)
                continue
        return self.default_model


# ---------------------------------------------------------------------------
# Built-in rule factories
# ---------------------------------------------------------------------------


def short_query_rule(*, threshold: int = 50, model: str) -> RoutingRule:
    """Route short queries to a cheaper model."""
    return RoutingRule(
        condition=lambda q, c: len(q) < threshold,
        model=model,
    )


def tool_heavy_rule(*, model: str, min_tools: int = 3) -> RoutingRule:
    """Route queries with many tools to a capable model."""
    return RoutingRule(
        condition=lambda q, c: c.get("tool_count", 0) >= min_tools,
        model=model,
    )
