# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Public API surface smoke tests for sagewai.autopilot.routing."""

from __future__ import annotations

import pytest


def test_routing_package_exports_goal_router():
    from sagewai.autopilot.routing import GoalRouter

    assert GoalRouter is not None


def test_routing_package_exports_confidence_config():
    from sagewai.autopilot.routing import ConfidenceConfig

    assert ConfidenceConfig is not None


def test_routing_package_exports_result_types():
    from sagewai.autopilot.routing import AutoRouted, PickerNeeded, SynthesisNeeded

    assert AutoRouted is not None
    assert PickerNeeded is not None
    assert SynthesisNeeded is not None


def test_routing_package_exports_ranked_blueprint():
    from sagewai.autopilot.routing import RankedBlueprint

    assert RankedBlueprint is not None


def test_routing_package_exports_slot_extractor_protocol():
    from sagewai.autopilot.routing import SlotExtractor

    assert SlotExtractor is not None


def test_routing_package_exports_rule_based_extractor():
    from sagewai.autopilot.routing import RuleBasedExtractor

    assert RuleBasedExtractor is not None


def test_routing_package_exports_routing_decision():
    from sagewai.autopilot.routing import RoutingDecision

    assert RoutingDecision is not None


def test_routing_package_exports_build_preview():
    from sagewai.autopilot.routing import build_preview

    assert callable(build_preview)


def test_autopilot_top_level_exports_goal_router():
    from sagewai.autopilot import GoalRouter

    assert GoalRouter is not None


def test_autopilot_top_level_exports_confidence_config():
    from sagewai.autopilot import ConfidenceConfig

    assert ConfidenceConfig is not None


def test_autopilot_top_level_exports_routing_result_types():
    from sagewai.autopilot import AutoRouted, PickerNeeded, SynthesisNeeded

    assert AutoRouted is not None
    assert PickerNeeded is not None
    assert SynthesisNeeded is not None


@pytest.mark.parametrize(
    "name",
    [
        "GoalRouter",
        "ConfidenceConfig",
        "AutoRouted",
        "PickerNeeded",
        "SynthesisNeeded",
        "RankedBlueprint",
        "SlotExtractor",
        "RuleBasedExtractor",
        "RoutingDecision",
        "build_preview",
    ],
)
def test_routing_all_contains_name(name: str):
    import sagewai.autopilot.routing as pkg

    assert name in pkg.__all__, f"{name!r} missing from sagewai.autopilot.routing.__all__"
