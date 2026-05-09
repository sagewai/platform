# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""The top-level :class:`Blueprint` declarative contract.

A :class:`Blueprint` composes every other piece of the autopilot
framework into one immutable object: slot specifications, a provider
requirement list, an :class:`AgentGraph`, success-criterion eval
references, training hooks, and (optionally) a Layer 5 learning-loop
configuration.

Blueprints are deliberately *declarative* — they contain no Python
callables and no runtime state. A blueprint plus concrete slot values
becomes a :class:`sagewai.autopilot.mission.Mission`.

v1 blueprints carry a concrete ``agent_graph``.  v1.1 blueprints carry
a ``composition`` list (pattern references) that a resolver materialises
into an ``agent_graph`` at retrieve/run time.  Exactly one of the two
must be present.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.sandbox.resolution import SandboxRequirements

from ._types import Mode
from .agent_graph import AgentGraph
from .errors import BlueprintValidationError, SlotValidationError
from .models import (
    EvalRef,
    LearningLoopConfig,
    ProviderRequirement,
    TrainingHook,
)
from .slots import SlotSpec
from .validators import ValidatorRegistry

QualityTier = Literal["gold", "curated", "provisional"]


class CompositionStep(BaseModel):
    """A single pattern reference in a v1.1 compositional blueprint."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    inputs: dict = Field(default_factory=dict)
    wraps: Optional[str] = None
    binds_to: Optional[str] = None


class Blueprint(BaseModel):
    """An autopilot blueprint — the declarative contract for one mission shape."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    quality_tier: QualityTier = "curated"

    # v1: concrete graph; v1.1: None until resolved by MissionDriver.
    agent_graph: Optional[AgentGraph] = None

    # v1.1: pattern-composition list; None for v1.
    composition: Optional[list[CompositionStep]] = None

    # v1 fields — optional so v1.1 blueprints can omit them (resolver fills at run-time).
    category: str = ""
    mode: Optional[Mode] = None
    providers_required: tuple[ProviderRequirement, ...] = ()

    example_goals: tuple[str, ...] = Field(default=(), min_length=0)
    required_slots: dict[str, SlotSpec] = Field(default_factory=dict)
    optional_slots: dict[str, SlotSpec] = Field(default_factory=dict)
    tools_required: tuple[str, ...] = ()
    success_criteria: EvalRef
    training_data_hooks: tuple[TrainingHook, ...] = ()
    learning_loop_target: LearningLoopConfig | None = None
    sandbox_requirements: SandboxRequirements | None = Field(
        default=None,
        description=(
            "Required sandbox posture for runs of this blueprint. "
            "Falls back through agent → project → SDK default if None."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> Blueprint:
        has_graph = self.agent_graph is not None
        has_composition = bool(self.composition)
        if has_graph == has_composition:
            raise ValueError(
                "Blueprint must have exactly one of `agent_graph` (v1) or "
                "`composition` (v1.1), not both and not neither."
            )
        return self

    @model_validator(mode="after")
    def _required_and_optional_slots_disjoint(self) -> Blueprint:
        collision = set(self.required_slots) & set(self.optional_slots)
        if collision:
            raise BlueprintValidationError(
                f"required_slots and optional_slots collide on: {sorted(collision)}"
            )
        return self

    def all_slot_specs(self) -> dict[str, SlotSpec]:
        """Return required and optional slots merged under one dict."""
        merged: dict[str, SlotSpec] = {}
        merged.update(self.required_slots)
        merged.update(self.optional_slots)
        return merged

    def validate_slots(
        self,
        values: dict[str, Any],
        *,
        registry: ValidatorRegistry,
    ) -> dict[str, Any]:
        """Validate a dict of slot values against this blueprint.

        Returns a new dict with:
          * all required slots validated and present,
          * defaults filled in for any missing optional slots,
          * unknown keys rejected as :class:`BlueprintValidationError`.

        Raises :class:`SlotValidationError` for per-slot failures and
        :class:`BlueprintValidationError` for blueprint-level ones.
        """
        all_specs = self.all_slot_specs()
        unknown = set(values) - set(all_specs)
        if unknown:
            raise BlueprintValidationError(f"unknown slot keys: {sorted(unknown)}")

        out: dict[str, Any] = {}
        for name, spec in all_specs.items():
            raw = values.get(name)
            out[name] = spec.validate_value(raw, slot_name=name, registry=registry)
        # Required slots must now be non-None.
        for name in self.required_slots:
            if out[name] is None:
                raise SlotValidationError(name, "required slot missing")
        return out
