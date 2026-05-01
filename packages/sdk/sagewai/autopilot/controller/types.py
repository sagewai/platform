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


class StepTelemetry(BaseModel):
    """Per-step harness telemetry — cost, tokens, routing decision.

    Populated when an agent step routes through
    :class:`~sagewai.harness.HarnessProxy`. Stays ``None`` on
    :class:`StepResult` for steps that ran under the direct-litellm
    fallback path or for deterministic/skipped steps.

    Attributes:
        cost_usd: Estimated cost of the LLM call in US dollars.
        input_tokens: Prompt tokens billed.
        output_tokens: Completion tokens billed.
        model_used: The model name actually used after harness routing.
            May differ from the requested model when policies / budget
            actions / classifier downgrade kick in.
        latency_ms: Wall-clock time of the LLM call (excludes routing
            overhead — measured around the backend's
            ``chat_completion`` call).
    """

    model_config = ConfigDict(frozen=True)

    cost_usd: float = Field(default=0.0, ge=0.0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model_used: str = Field(min_length=1)
    latency_ms: float = Field(default=0.0, ge=0.0)


class StepResult(BaseModel):
    """Record of one agent node's execution.

    Attributes:
        node_id: The agent node ID this step represents.
        status: One of ``"completed"``, ``"skipped"``, or ``"failed"``.
        output_preview: Short truncated output for UI/log display
            (≤200 chars). May be ``None`` for deterministic steps.
        output: Full LLM output content for LLM steps. ``None`` for
            deterministic steps, skipped steps, or steps that ran the
            direct-litellm fallback path before harness wiring landed.
            Curator builds training samples from this field when
            available, falling back to ``output_preview``.
        messages: Full conversation messages (system + user +
            assistant) for this step. ``None`` outside the harness
            path. ShareGPT-format training samples use this for
            multi-turn conversations.
        telemetry: Per-step harness telemetry (cost, tokens, model
            used, latency). ``None`` outside the harness path.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    output_preview: str | None = None
    output: str | None = None
    messages: tuple[dict, ...] | None = None
    telemetry: StepTelemetry | None = None


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
