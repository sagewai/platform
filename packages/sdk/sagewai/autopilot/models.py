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
:mod:`sagewai.autopilot.slots`, and :mod:`sagewai.autopilot.mission`.
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
