# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Autopilot self-healing ops — Layer 6.

Public API surface:

- :class:`HealingPolicy` — configurable thresholds for all detection rules.
- :class:`RotateProvider` — recommend switching to a different LLM provider.
- :class:`PauseBudget` — recommend pausing a mission's budget.
- :class:`AlertOperator` — send a structured alert to the operator.
- :class:`RetryMission` — recommend retrying a failed mission with backoff.
- :data:`HealingAction` — discriminated union of all action variants.
- :class:`HealthMonitor` — stateful sliding-window event processor.
- :class:`HealingEngine` — pure evaluate() recommendation function.
- :class:`MissionContext` — per-mission metadata for cost/timeout detection.
"""

from __future__ import annotations

from .engine import HealingEngine, MissionContext
from .monitor import HealthMonitor, HealthSignal
from .types import (
    AlertOperator,
    HealingAction,
    HealingPolicy,
    PauseBudget,
    RetryMission,
    RotateProvider,
)

__all__ = [
    # Policy
    "HealingPolicy",
    # Action variants
    "RotateProvider",
    "PauseBudget",
    "AlertOperator",
    "RetryMission",
    "HealingAction",
    # Monitor
    "HealthMonitor",
    "HealthSignal",
    # Engine
    "HealingEngine",
    "MissionContext",
]
