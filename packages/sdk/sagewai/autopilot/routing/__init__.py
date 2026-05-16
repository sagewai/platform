# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sagewai Autopilot routing — goal intake, confidence gating, slot extraction.

Public API surface:

- :class:`GoalRouter` — async orchestrator that maps a plain-English goal
  to a :class:`RoutingResult`.
- :class:`ConfidenceConfig` — configurable threshold dataclass.
- :class:`RoutingDecision` — enum of gating outcomes.
- :class:`AutoRouted`, :class:`PickerNeeded`, :class:`SynthesisNeeded` —
  the three result variants.
- :class:`RankedBlueprint` — a scored blueprint candidate.
- :class:`SlotExtractor` — Protocol for slot extractor implementations.
- :class:`RuleBasedExtractor` — built-in rule-based stub extractor.
- :func:`build_preview` — plan-card builder.
"""

from __future__ import annotations

from .confidence import ConfidenceConfig, RoutingDecision
from .extractor import RuleBasedExtractor, SlotExtractor
from .preview import build_preview
from .router import GoalRouter
from .types import AutoRouted, PickerNeeded, RankedBlueprint, RoutingResult, SynthesisNeeded

__all__ = [
    # Router
    "GoalRouter",
    # Config
    "ConfidenceConfig",
    "RoutingDecision",
    # Result types
    "AutoRouted",
    "PickerNeeded",
    "SynthesisNeeded",
    "RankedBlueprint",
    "RoutingResult",
    # Extractor
    "SlotExtractor",
    "RuleBasedExtractor",
    # Preview
    "build_preview",
]
