# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Frozen result types for the autopilot controller.

``StepResult``
    Per-node execution record inside a :class:`MissionRunResult`. The
    ``output_preview`` is a short string summary — real output from the
    stub is always ``"[stub]"`` since no LLM is called.

``MissionRunResult``
    Immutable snapshot of a completed (or failed) mission run. Carries
    the mission id, terminal status, ordered step log, wall-clock
    duration, and an optional error string.

``ControllerConfig``
    Injectable configuration for :class:`AutopilotController`. Holds
    the project id, registry, and default slot values applied when
    :meth:`AutopilotController.start_mission` auto-routes a goal.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sagewai.autopilot.validators import ValidatorRegistry, default_registry


class StepResult(BaseModel):
    """Record of one agent node's execution."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    output_preview: str | None = None


class MissionRunResult(BaseModel):
    """Immutable result of a :class:`MissionDriver` execution."""

    model_config = ConfigDict(frozen=True)

    mission_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    steps: tuple[StepResult, ...] = ()
    duration_seconds: float = Field(ge=0.0)
    error: str | None = None


class ControllerConfig(BaseModel):
    """Injectable configuration for :class:`AutopilotController`.

    Attributes:
        project_id: Multi-tenant project scope for created missions.
        registry: Slot validator registry used when binding blueprints.
        default_slots: Slot values automatically merged into extracted
            slots before mission creation.  Useful in tests to supply
            required slots that the rule-based extractor cannot infer
            from a short goal string.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    project_id: str = Field(default="default", min_length=1)
    registry: ValidatorRegistry = Field(default_factory=lambda: default_registry)
    default_slots: dict[str, Any] = Field(default_factory=dict)
