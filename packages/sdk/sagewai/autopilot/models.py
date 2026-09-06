# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Auxiliary pydantic models composed into :class:`Blueprint`.

These are deliberately small: the "interesting" logic lives in
:mod:`sagewai.autopilot.blueprint`, :mod:`sagewai.autopilot.agent_graph`,
and :mod:`sagewai.autopilot.slots`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._types import Operator


class ProviderRequirement(BaseModel):
    """A declaration that a blueprint needs an LLM provider of some kind."""

    model_config = ConfigDict(frozen=True)

    role: str  # e.g. "summarizer", "classifier"
    capability: str  # e.g. "reasoning", "classification"
    tier: str  # e.g. "small", "medium", "large"
    fine_tune_target: bool = False  # Layer 5 learning-loop hook


class Metric(BaseModel):
    """A single numeric success-criterion check.

    Accepts both v1 shape ``{name, op, value}`` and v1.1 shape
    ``{name, target}`` (normalised to op=">=", value=target).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    op: Operator = Operator.GE
    value: float

    @model_validator(mode="before")
    @classmethod
    def _normalise_v1_1_shape(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        # v1.1 shape: {name, target} → {name, op=">=", value=target}
        if "value" not in data and "target" in data:
            data = {**data, "value": data["target"], "op": data.get("op", ">=")}
        return data


class EvalRef(BaseModel):
    """Points a blueprint at a managed eval dataset + metric gates.

    ``dataset_id`` is optional for v1.1 compositional blueprints that
    inherit the eval dataset from the resolved pattern.
    """

    model_config = ConfigDict(frozen=True)

    dataset_id: str = ""
    metrics: tuple[Metric, ...]


class TrainingHook(BaseModel):
    """Captures runs into a training dataset under a quality filter.

    Accepts both v1 shape ``{event, dataset, format}`` and v1.1 shape
    ``{hook, target, filter, destination}`` (normalised to v1 fields).
    """

    model_config = ConfigDict(frozen=True)

    event: str  # e.g. "summarizer.completed"
    dataset: str  # may contain {project_id}, {document_type}
    format: str = "alpaca"  # "alpaca", "sharegpt", "classification"
    quality_filter: str | None = None  # e.g. "user_rating >= 4"

    @model_validator(mode="before")
    @classmethod
    def _normalise_v1_1_shape(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        # v1.1 field aliases: hook→event, target/destination→dataset, filter→quality_filter
        if "event" not in out and "hook" in out:
            out["event"] = out["hook"]
        if "dataset" not in out:
            if "destination" in out:
                out["dataset"] = out["destination"]
            elif "target" in out:
                out["dataset"] = out["target"]
        if "quality_filter" not in out and "filter" in out:
            out["quality_filter"] = out["filter"]
        return out


class LearningLoopConfig(BaseModel):
    """Layer 5 configuration for automatic fine-tune + promote."""

    model_config = ConfigDict(frozen=True)

    trigger_after_labeled_samples: int = Field(gt=0)
    base_model: str
    eval_gate_dataset_id: str
    promotion_criteria: str  # e.g. "accuracy >= 0.92 AND cost <= ..."
    fine_tune_method: str = "unsloth"
    deploy_as: str = "ollama"


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
            assistant + tool turns) for this step. ``None`` outside
            the harness path. ShareGPT-format training samples use
            this for multi-turn conversations.
        telemetry: Per-step harness telemetry (cost, tokens, model
            used, latency). ``None`` outside the harness path.
        tool_calls: Names of tools actually invoked during this step,
            in call order. ``None`` when no tool calls were made.
            Populated by the tool-call loop in ``AgentExecutor`` when
            the agent has ``tools`` configured. Useful for telemetry
            and Curator training-data labelling.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    output_preview: str | None = None
    output: str | None = None
    messages: tuple[dict, ...] | None = None
    telemetry: StepTelemetry | None = None
    tool_calls: tuple[str, ...] | None = None


class MissionRunResult(BaseModel):
    """Immutable result of a :class:`MissionDriver` execution."""

    model_config = ConfigDict(frozen=True)

    mission_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    steps: tuple[StepResult, ...] = ()
    duration_seconds: float = Field(ge=0.0)
    error: str | None = None
