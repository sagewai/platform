# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Autopilot controller — mission orchestration and execution.

Public API surface:

- :class:`MissionRunResult` — frozen result of a completed or failed run.
- :class:`StepResult` — per-node result inside a :class:`MissionRunResult`.
- :class:`StepTelemetry` — per-step harness telemetry (cost, tokens, routing).
- :class:`ControllerConfig` — injectable configuration for the controller.
- :class:`AgentExecutor` — executes a single agent node via LiteLLM.
- :class:`ExecutorConfig` — configuration for :class:`AgentExecutor`.
"""

from __future__ import annotations

from .controller import AutopilotController
from .driver import MissionDriver
from .executor import AgentExecutor, ExecutorConfig
from .fleet_adapter import FleetMissionAdapter
from .scheduler import CronParser, MissionScheduler, ScheduledMission
from .types import ControllerConfig, MissionRunResult, StepResult, StepTelemetry

__all__ = [
    "AutopilotController",
    "MissionDriver",
    "AgentExecutor",
    "ExecutorConfig",
    "ControllerConfig",
    "MissionRunResult",
    "StepResult",
    "StepTelemetry",
    "FleetMissionAdapter",
    "MissionScheduler",
    "ScheduledMission",
    "CronParser",
]
