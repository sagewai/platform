# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Frozen types for the autopilot self-healing ops layer.

``HealingPolicy``
    Configurable thresholds that control when the :class:`HealingEngine`
    fires each detection rule.

``RotateProvider``
    Recommends switching to a different LLM provider for a blueprint.

``PauseBudget``
    Recommends pausing a mission's budget until an operator reviews it.

``AlertOperator``
    Sends a structured alert to the operator.

``RetryMission``
    Recommends retrying a failed mission with an exponential backoff.

``HealingAction``
    Discriminated union of all action variants; keyed on ``kind``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealingPolicy(BaseModel):
    """Configurable thresholds for the healing engine.

    Attributes:
        failure_threshold: Number of consecutive FAILED missions for the
            same blueprint before a :class:`RotateProvider` action is
            recommended.
        cost_spike_multiplier: When ``actual_cost > estimated_cost *
            cost_spike_multiplier`` a :class:`PauseBudget` +
            :class:`AlertOperator` pair is emitted.
        success_rate_window: Sliding window size (number of most-recent
            results) used for drift detection.
        success_rate_minimum: If the success rate over the window falls
            below this value a :class:`AlertOperator` (critical) is fired.
        duration_spike_multiplier: When ``actual_duration >
            estimated_duration * duration_spike_multiplier`` a
            :class:`RetryMission` + :class:`AlertOperator` pair is emitted.
    """

    model_config = ConfigDict(frozen=True)

    failure_threshold: int = Field(default=3, ge=1)
    cost_spike_multiplier: float = Field(default=2.0, gt=1.0)
    success_rate_window: int = Field(default=20, ge=2)
    success_rate_minimum: float = Field(default=0.8, ge=0.0, le=1.0)
    duration_spike_multiplier: float = Field(default=3.0, gt=1.0)


# ---------------------------------------------------------------------------
# Action variants
# ---------------------------------------------------------------------------


class RotateProvider(BaseModel):
    """Recommend switching to a different LLM provider."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["rotate_provider"] = "rotate_provider"
    blueprint_id: str = Field(min_length=1)
    suggested_provider: str = Field(default="fallback", min_length=1)


class PauseBudget(BaseModel):
    """Recommend pausing a mission's budget until reviewed."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["pause_budget"] = "pause_budget"
    mission_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AlertOperator(BaseModel):
    """Send a structured alert to the operator."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["alert_operator"] = "alert_operator"
    message: str = Field(min_length=1)
    severity: Literal["info", "warning", "critical"] = "warning"


class RetryMission(BaseModel):
    """Recommend retrying a failed mission with backoff."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["retry_mission"] = "retry_mission"
    mission_id: str = Field(min_length=1)
    backoff_seconds: float = Field(default=30.0, ge=0.0)


# Discriminated union — keyed on the ``kind`` literal field.
HealingAction = Annotated[
    RotateProvider | PauseBudget | AlertOperator | RetryMission,
    Field(discriminator="kind"),
]
