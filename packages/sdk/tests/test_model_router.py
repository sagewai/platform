# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for ModelRouter — dynamic model selection based on rules."""

from __future__ import annotations

from sagewai.core.model_router import ModelRouter, RoutingRule


class TestRoutingRule:
    def test_simple_rule(self):
        rule = RoutingRule(
            condition=lambda q, c: len(q) < 50,
            model="gpt-4o-mini",
        )
        assert rule.model == "gpt-4o-mini"
        assert rule.condition("short query", {}) is True
        assert rule.condition("x" * 100, {}) is False


class TestModelRouter:
    def test_matches_first_rule(self):
        router = ModelRouter(
            rules=[
                RoutingRule(condition=lambda q, c: len(q) < 20, model="gpt-4o-mini"),
                RoutingRule(condition=lambda q, c: True, model="gpt-4o"),
            ],
            default_model="gpt-4o",
        )
        assert router.select_model("hi", {}) == "gpt-4o-mini"

    def test_falls_through_to_default(self):
        router = ModelRouter(
            rules=[
                RoutingRule(condition=lambda q, c: False, model="gpt-4o-mini"),
            ],
            default_model="gpt-4o",
        )
        assert router.select_model("any query", {}) == "gpt-4o"

    def test_empty_rules_uses_default(self):
        router = ModelRouter(rules=[], default_model="claude-3-sonnet")
        assert router.select_model("anything", {}) == "claude-3-sonnet"

    def test_context_available_in_condition(self):
        router = ModelRouter(
            rules=[
                RoutingRule(
                    condition=lambda q, c: c.get("has_tools", False),
                    model="gpt-4o",
                ),
            ],
            default_model="gpt-4o-mini",
        )
        assert router.select_model("query", {"has_tools": True}) == "gpt-4o"
        assert router.select_model("query", {"has_tools": False}) == "gpt-4o-mini"

    def test_multiple_rules_first_match_wins(self):
        router = ModelRouter(
            rules=[
                RoutingRule(condition=lambda q, c: "urgent" in q, model="gpt-4o"),
                RoutingRule(condition=lambda q, c: len(q) < 50, model="gpt-4o-mini"),
            ],
            default_model="gpt-4o",
        )
        # Both rules match, first wins
        assert router.select_model("urgent short", {}) == "gpt-4o"
        # Only second matches
        assert router.select_model("short", {}) == "gpt-4o-mini"


class TestBuiltInRules:
    def test_short_query_rule(self):
        from sagewai.core.model_router import short_query_rule

        rule = short_query_rule(threshold=30, model="gpt-4o-mini")
        assert rule.condition("hi there", {}) is True
        assert rule.condition("x" * 50, {}) is False

    def test_tool_heavy_rule(self):
        from sagewai.core.model_router import tool_heavy_rule

        rule = tool_heavy_rule(model="gpt-4o")
        assert rule.condition("query", {"tool_count": 5}) is True
        assert rule.condition("query", {"tool_count": 0}) is False
        assert rule.condition("query", {}) is False
