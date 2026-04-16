# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Autopilot — goal-driven agent mission framework.

Public API surface for the declarative framework. This subpackage
contains zero production blueprints — all real blueprints live on the
proprietary Sagewai LLM service. Only synthetic test fixtures ever
appear in the open-source repo (see ``tests/autopilot/fixtures.py``).
"""

from __future__ import annotations

from ._types import AgentKind, MissionState, Mode, Operator
from .agent_graph import Agent, AgentGraph, Branch
from .blueprint import Blueprint
from .errors import (
    AgentGraphError,
    AutopilotError,
    BlueprintValidationError,
    MissionLifecycleError,
    SlotValidationError,
)
from .mission import Mission
from .models import (
    EvalRef,
    LearningLoopConfig,
    Metric,
    ProviderRequirement,
    TrainingHook,
)
from .slots import SlotSpec
from .validators import ValidatorRegistry, default_registry
from .routing import (
    AutoRouted,
    ConfidenceConfig,
    GoalRouter,
    PickerNeeded,
    RankedBlueprint,
    RoutingDecision,
    RoutingResult,
    RuleBasedExtractor,
    SlotExtractor,
    SynthesisNeeded,
    build_preview,
)
from .controller import (
    AutopilotController,
    ControllerConfig,
    MissionDriver,
    MissionRunResult,
    StepResult,
)

__all__ = [
    # Errors
    "AutopilotError",
    "SlotValidationError",
    "BlueprintValidationError",
    "AgentGraphError",
    "MissionLifecycleError",
    # Enums
    "Mode",
    "AgentKind",
    "Operator",
    "MissionState",
    # Validators
    "ValidatorRegistry",
    "default_registry",
    # Aux models
    "ProviderRequirement",
    "Metric",
    "EvalRef",
    "TrainingHook",
    "LearningLoopConfig",
    # Slots
    "SlotSpec",
    # Agent graph
    "Agent",
    "Branch",
    "AgentGraph",
    # Top-level
    "Blueprint",
    "Mission",
    # Routing
    "GoalRouter",
    "ConfidenceConfig",
    "RoutingDecision",
    "AutoRouted",
    "PickerNeeded",
    "SynthesisNeeded",
    "RankedBlueprint",
    "RoutingResult",
    "SlotExtractor",
    "RuleBasedExtractor",
    "build_preview",
    # Controller
    "AutopilotController",
    "MissionDriver",
    "ControllerConfig",
    "MissionRunResult",
    "StepResult",
]
