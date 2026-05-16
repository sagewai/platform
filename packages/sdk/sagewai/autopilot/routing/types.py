# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""RoutingResult discriminated union and supporting types.

Three possible outcomes from :class:`sagewai.autopilot.routing.GoalRouter`:

``AutoRouted``
    The top candidate scored above the high-confidence threshold. The
    router picked it automatically, extracted slot values, and built a
    plan preview. The caller should present the preview for user approval
    then hand ``ranked.blueprint_json`` and ``slots`` to the
    ``AutopilotController`` (Plan 4).

``PickerNeeded``
    The top candidate scored in the ambiguous band. The caller should
    surface a picker UI (Plan 7) showing the top-3 candidates ordered
    by score so the user can choose.

``SynthesisNeeded``
    No candidate met the minimum threshold. The caller should either ask
    the user a clarifying question or call
    ``SagewaiLLMClient.generate_blueprint(goal)`` to synthesise a fresh
    blueprint on the server.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RankedBlueprint(BaseModel):
    """A scored blueprint candidate returned by the retrieval service."""

    model_config = ConfigDict(frozen=True)

    blueprint_json: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    quality_tier: Optional[str] = None


class AutoRouted(BaseModel):
    """The router selected a blueprint automatically (score >= high threshold)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["auto_routed"] = "auto_routed"
    ranked: RankedBlueprint
    slots: dict[str, Any]
    preview: str = Field(min_length=1)


class PickerNeeded(BaseModel):
    """Score is in the ambiguous band — the user must choose from top candidates."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["picker_needed"] = "picker_needed"
    candidates: tuple[RankedBlueprint, ...] = Field(min_length=1)


class SynthesisNeeded(BaseModel):
    """No candidate passed the minimum threshold — synthesis or clarification required."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["synthesis_needed"] = "synthesis_needed"
    goal: str = Field(min_length=1)


#: Discriminated union of all possible routing outcomes.
RoutingResult = AutoRouted | PickerNeeded | SynthesisNeeded
