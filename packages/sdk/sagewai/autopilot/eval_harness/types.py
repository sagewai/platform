# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Frozen Pydantic types for the autopilot eval harness.

``GoldenGoal``
    A single test fixture: a plain-English goal string, the expected
    blueprint ID (or ``None`` for synthesis outcomes), and the expected
    confidence band string.

``GoldenGoalSet``
    A versioned, immutable collection of :class:`GoldenGoal` entries with
    metadata for audit-log traceability.

``EvalReport``
    The frozen output of a :class:`EvalHarness` run. Carries top-1 accuracy,
    band accuracy, false-positive rate, and wall-clock duration.

``EvalConfig``
    Confidence thresholds injected into the harness at construction time.
    Defaults match the production :class:`ConfidenceConfig` values so that
    CI exercises the live routing logic.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The three expected routing outcome bands.
Band = Literal["auto_route", "picker", "synthesis"]


class GoldenGoal(BaseModel):
    """A single (goal, expected_blueprint_id, expected_band) eval fixture.

    Attributes:
        goal: Plain-English goal string to route.
        expected_blueprint_id: The blueprint ID the router should select as
            top-1, or ``None`` when the expected outcome is synthesis.
        expected_band: Which confidence band the router should land in.
    """

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    expected_blueprint_id: str | None
    expected_band: Band


class GoldenGoalSet(BaseModel):
    """A versioned, immutable collection of golden goals.

    Attributes:
        version: Semver-style version string for the fixture set (e.g. ``"1.0.0"``).
        created_at: ISO-8601 UTC timestamp; auto-populated on construction.
        goals: One or more :class:`GoldenGoal` entries.
        description: Optional human-readable label for the set.
    """

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc)
    )
    goals: tuple[GoldenGoal, ...] = Field(min_length=1)
    description: str = ""


class EvalReport(BaseModel):
    """Frozen metrics report produced by a single :class:`EvalHarness` run.

    Attributes:
        total_goals: Number of goals evaluated.
        top1_accuracy: Fraction of goals where the router's top-1 blueprint
            ID matched the expected ID (synthesis goals counted as correct
            when the router returns ``SynthesisNeeded``).
        band_accuracy: Fraction of goals where the router's outcome band
            matched the expected band.
        false_positive_rate: Fraction of synthesis-expected goals where the
            router returned a non-synthesis result (auto-routed or picker).
        duration_seconds: Wall-clock time for the full run.
    """

    model_config = ConfigDict(frozen=True)

    total_goals: int = Field(ge=1)
    top1_accuracy: float = Field(ge=0.0, le=1.0)
    band_accuracy: float = Field(ge=0.0, le=1.0)
    false_positive_rate: float = Field(ge=0.0, le=1.0)
    duration_seconds: float = Field(ge=0.0)


class EvalConfig(BaseModel):
    """Confidence thresholds to use during an eval run.

    Defaults match the production :class:`ConfidenceConfig` values so that
    CI always exercises live routing thresholds.  Override for threshold
    sensitivity sweeps.

    Attributes:
        auto_route_threshold: Minimum score for automatic routing (default 0.85).
        picker_threshold: Minimum score for the picker band (default 0.65).
        retrieve_k: Number of blueprint candidates to request per goal (default 5).
    """

    model_config = ConfigDict(frozen=True)

    auto_route_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    picker_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    retrieve_k: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> EvalConfig:
        if self.picker_threshold >= self.auto_route_threshold:
            raise ValueError(
                f"picker_threshold ({self.picker_threshold}) must be strictly less than "
                f"auto_route_threshold ({self.auto_route_threshold})"
            )
        return self
