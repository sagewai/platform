# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Confidence gating for autopilot blueprint routing.

The :func:`gate` function maps a ranked candidate list to one of three
:class:`RoutingDecision` outcomes based on configurable score thresholds
in :class:`ConfidenceConfig`.

Design decisions:

- Thresholds are *inclusive* at the high end of each band:
  ``score >= auto_route_threshold`` → AUTO_ROUTE,
  ``picker_threshold <= score < auto_route_threshold`` → PICKER,
  ``score < picker_threshold`` → SYNTHESIZE.
- An empty candidate list always produces SYNTHESIZE.
- :class:`ConfidenceConfig` is a frozen Pydantic model so it can be
  safely shared between instances and serialised to JSON for audit logs.
"""

from __future__ import annotations

import enum
import os

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import RankedBlueprint


def _env_float(name: str, default: float) -> float:
    """Read *name* from the environment and parse as float, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class RoutingDecision(enum.Enum):
    """The three possible outcomes from :func:`gate`."""

    AUTO_ROUTE = "auto_route"
    PICKER = "picker"
    SYNTHESIZE = "synthesize"


class ConfidenceConfig(BaseModel):
    """Configurable confidence thresholds for routing decisions.

    Attributes:
        auto_route_threshold: Minimum score for automatic blueprint
            selection.  Scores *at or above* this value are auto-routed.
            Default: 0.85.
        picker_threshold: Minimum score to show the user a picker.
            Scores *at or above* this value (but below
            ``auto_route_threshold``) trigger the picker flow.
            Default: 0.65.
        picker_top_k: Maximum number of candidates to include in a
            :class:`PickerNeeded` result.  Default: 3.
    """

    model_config = ConfigDict(frozen=True)

    auto_route_threshold: float = Field(
        default_factory=lambda: _env_float("AUTOPILOT_AUTO_ROUTE_THRESHOLD", 0.85),
        ge=0.0,
        le=1.0,
    )
    picker_threshold: float = Field(
        default_factory=lambda: _env_float("AUTOPILOT_PICKER_THRESHOLD", 0.65),
        ge=0.0,
        le=1.0,
    )
    picker_top_k: int = Field(default=3, gt=0)

    @model_validator(mode="after")
    def _auto_above_picker(self) -> ConfidenceConfig:
        if self.auto_route_threshold <= self.picker_threshold:
            raise ValueError(
                f"auto_route_threshold ({self.auto_route_threshold}) must be "
                f"strictly above picker_threshold ({self.picker_threshold})"
            )
        return self


def gate(
    candidates: tuple[RankedBlueprint, ...] | list[RankedBlueprint],
    config: ConfidenceConfig,
) -> RoutingDecision:
    """Map ranked candidates to a routing decision.

    Only the *first* candidate's score is used for the threshold check —
    the caller is responsible for ensuring the list is sorted descending
    by score (as the hosted service guarantees).

    Args:
        candidates: Scored blueprint candidates, best-first.
        config: Threshold configuration.

    Returns:
        The appropriate :class:`RoutingDecision` for the caller.
    """
    if not candidates:
        return RoutingDecision.SYNTHESIZE

    top_score = candidates[0].score
    if top_score >= config.auto_route_threshold:
        return RoutingDecision.AUTO_ROUTE
    if top_score >= config.picker_threshold:
        return RoutingDecision.PICKER
    return RoutingDecision.SYNTHESIZE
